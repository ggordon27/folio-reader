#!/usr/bin/env python3
"""
GDrive EPUB Reader & Gemini Study Companion
Local Web Server (Python 3 HTTP Server + SQLite + Google Drive CLI + Gemini Interactions API)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import os
import posixpath
import re
import secrets
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("FOLIO_DATA_DIR", str(BASE_DIR / "data")))
BOOKS_DIR = DATA_DIR / "books"
STATIC_DIR = BASE_DIR / "static"
DB_PATH = DATA_DIR / "reader.db"

DATA_DIR.mkdir(parents=True, exist_ok=True)
BOOKS_DIR.mkdir(parents=True, exist_ok=True)
STATIC_DIR.mkdir(parents=True, exist_ok=True)

GDRIVE_BIN = shutil.which("gdrive") or "/Users/dr_geoff/.gemini/antigravity/bin/gdrive"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1/interactions"

# Ensure extra mime types
mimetypes.add_type("application/xhtml+xml", ".xhtml")
mimetypes.add_type("application/epub+zip", ".epub")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/otf", ".otf")


def get_db():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    with conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );

            CREATE TABLE IF NOT EXISTS books (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                title TEXT,
                author TEXT,
                publisher TEXT,
                cover_href TEXT,
                opf_dir TEXT,
                parent_folder_id TEXT,
                size INTEGER,
                modified_time TEXT,
                web_view_link TEXT,
                downloaded INTEGER DEFAULT 0,
                last_chapter_index INTEGER DEFAULT 0,
                scroll_percent REAL DEFAULT 0.0,
                last_opened_at TEXT,
                annotations_drive_id TEXT,
                last_synced_at TEXT,
                manifest_json TEXT
            );

            CREATE TABLE IF NOT EXISTS annotations (
                id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                chapter_index INTEGER NOT NULL,
                chapter_href TEXT NOT NULL,
                chapter_title TEXT,
                selected_text TEXT NOT NULL,
                note TEXT DEFAULT '',
                gemini_response TEXT DEFAULT '',
                color TEXT DEFAULT 'yellow',
                occurrence_index INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS chats (
                id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                chapter_index INTEGER,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                selected_text TEXT DEFAULT '',
                interaction_id TEXT DEFAULT '',
                model TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS bookmarks (
                id TEXT PRIMARY KEY,
                book_id TEXT NOT NULL,
                chapter_index INTEGER NOT NULL,
                chapter_title TEXT DEFAULT '',
                scroll_percent REAL DEFAULT 0.0,
                label TEXT DEFAULT '',
                excerpt TEXT DEFAULT '',
                created_at TEXT NOT NULL
            );
            """
        )
        # Ensure folder_path, tags, and page_number columns exist
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(books)").fetchall()]
        if "folder_path" not in cols:
            conn.execute("ALTER TABLE books ADD COLUMN folder_path TEXT DEFAULT ''")
        if "tags" not in cols:
            conn.execute("ALTER TABLE books ADD COLUMN tags TEXT DEFAULT '[]'")
        ann_cols = [r["name"] for r in conn.execute("PRAGMA table_info(annotations)").fetchall()]
        if "page_number" not in ann_cols:
            conn.execute("ALTER TABLE annotations ADD COLUMN page_number TEXT DEFAULT ''")

        seed_file = BASE_DIR / "tag_seed.json"
        if seed_file.exists():
            try:
                seed_map = json.loads(seed_file.read_text(encoding="utf-8"))
                for bid, tags in seed_map.items():
                    if bid and tags:
                        conn.execute(
                            "UPDATE books SET tags = ? WHERE id = ? AND (tags IS NULL OR tags = '' OR tags = '[]')",
                            (json.dumps(tags), bid),
                        )
            except Exception:
                pass
        conn.execute(
            "DELETE FROM books WHERE LOWER(name) LIKE '%getting started with adobe digital editions%' OR LOWER(title) LIKE '%getting started with adobe digital editions%'"
        )
    conn.close()


def get_setting(key: str, default: str = "") -> str:
    conn = get_db()
    try:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row and row["value"] is not None:
            return row["value"]
    finally:
        conn.close()
    return default


def set_setting(key: str, value: str):
    conn = get_db()
    try:
        with conn:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    finally:
        conn.close()


def resolve_gemini_api_key() -> str:
    # 1. Check DB setting
    db_key = get_setting("gemini_api_key", "").strip()
    if db_key:
        return db_key
    # 2. Check environment variables
    for env_var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(env_var, "").strip()
        if val:
            return val
    # 3. Check ~/.config/gemini-api/config.yaml if present
    cfg_path = Path.home() / ".config" / "gemini-api" / "config.yaml"
    if cfg_path.exists():
        try:
            text = cfg_path.read_text(encoding="utf-8")
            m = re.search(r"api[_-]?key\s*:\s*['\"]?([A-Za-z0-9_\-]+)['\"]?", text)
            if m:
                return m.group(1)
        except Exception:
            pass
    return ""


def run_gdrive(args: list, timeout: int = 60) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"/Users/dr_geoff/.gemini/antigravity/bin:{env.get('PATH', '')}"
    cmd = [GDRIVE_BIN] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


# --- Passcode Session Auth (for Railway / Cloud Deployment) ---

def get_active_passcode() -> str:
    env_pc = os.environ.get("FOLIO_PASSCODE", "").strip()
    if env_pc:
        return env_pc
    return get_setting("folio_passcode", "").strip()


def _get_session_secret() -> bytes:
    secret = os.environ.get("FOLIO_SESSION_SECRET", "").strip()
    if not secret:
        secret = get_setting("folio_session_secret", "").strip()
    if not secret:
        secret = secrets.token_hex(32)
        set_setting("folio_session_secret", secret)
    return secret.encode("utf-8")


def make_session_token(passcode: str) -> str:
    exp = int(time.time()) + 30 * 86400
    payload = f"{exp}.{hashlib.sha256(passcode.encode('utf-8')).hexdigest()[:16]}"
    sig = hmac.new(_get_session_secret(), payload.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def verify_session_token(token: str, passcode: str) -> bool:
    if not token or not passcode:
        return False
    parts = token.split(".")
    if len(parts) != 3:
        return False
    exp_s, pc_hash, sig = parts
    try:
        if int(exp_s) < int(time.time()):
            return False
    except ValueError:
        return False
    expected_hash = hashlib.sha256(passcode.encode("utf-8")).hexdigest()[:16]
    if not hmac.compare_digest(pc_hash, expected_hash):
        return False
    expected_sig = hmac.new(_get_session_secret(), f"{exp_s}.{pc_hash}".encode("utf-8"), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, expected_sig)


# --- Google Drive OAuth2 REST API v3 Client (for Railway / Cloud) ---

_DRIVE_TOKEN_CACHE = {"access_token": "", "expires_at": 0.0}


def get_drive_oauth_config() -> dict:
    return {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", "").strip() or get_setting("google_client_id", "").strip(),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", "").strip() or get_setting("google_client_secret", "").strip(),
        "refresh_token": os.environ.get("GOOGLE_REFRESH_TOKEN", "").strip() or get_setting("google_refresh_token", "").strip(),
    }


def has_drive_oauth() -> bool:
    cfg = get_drive_oauth_config()
    return bool(cfg["client_id"] and cfg["client_secret"] and cfg["refresh_token"])


def get_drive_access_token() -> str:
    if _DRIVE_TOKEN_CACHE["access_token"] and time.time() < _DRIVE_TOKEN_CACHE["expires_at"] - 60:
        return _DRIVE_TOKEN_CACHE["access_token"]
    cfg = get_drive_oauth_config()
    if not (cfg["client_id"] and cfg["client_secret"] and cfg["refresh_token"]):
        raise RuntimeError("Google Drive OAuth is not configured.")
    body = urllib.parse.urlencode(
        {
            "client_id": cfg["client_id"],
            "client_secret": cfg["client_secret"],
            "refresh_token": cfg["refresh_token"],
            "grant_type": "refresh_token",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google OAuth token refresh failed ({e.code}): {err_body}")
    tok = data["access_token"]
    _DRIVE_TOKEN_CACHE["access_token"] = tok
    _DRIVE_TOKEN_CACHE["expires_at"] = time.time() + float(data.get("expires_in", 3500))
    return tok


def drive_rest_json(url: str, method: str = "GET", body_bytes: bytes = None, content_type: str = "application/json") -> dict:
    token = get_drive_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    if body_bytes is not None:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Google Drive API error ({e.code}): {err_body}")


def drive_rest_download(file_id: str, out_path: Path):
    token = get_drive_access_token()
    url = f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id)}?alt=media"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        out_path.write_bytes(resp.read())


def drive_rest_upload_file(name: str, data_bytes: bytes, mime_type: str, parent_id: str = "", existing_id: str = "", target_mime: str = "") -> dict:
    token = get_drive_access_token()
    boundary = f"folio_{secrets.token_hex(12)}"
    meta = {"name": name}
    if target_mime:
        meta["mimeType"] = target_mime
    if parent_id and not existing_id:
        meta["parents"] = [parent_id]

    parts = [
        f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n{json.dumps(meta)}\r\n".encode("utf-8"),
        f"--{boundary}\r\nContent-Type: {mime_type}\r\n\r\n".encode("utf-8") + data_bytes + b"\r\n",
        f"--{boundary}--\r\n".encode("utf-8"),
    ]
    body = b"".join(parts)
    if existing_id:
        url = f"https://www.googleapis.com/upload/drive/v3/files/{urllib.parse.quote(existing_id)}?uploadType=multipart&fields=id,name,webViewLink"
        method = "PATCH"
    else:
        url = "https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id,name,webViewLink"
        method = "POST"
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/related; boundary={boundary}",
        },
        method=method,
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def drive_rest_search_epubs() -> list:
    files = []
    page_token = ""
    while True:
        params = {
            "q": "trashed = false and (mimeType = 'application/epub+zip' or name contains '.epub')",
            "fields": "nextPageToken, files(id, name, size, modifiedTime, webViewLink, parents)",
            "pageSize": "500",
        }
        if page_token:
            params["pageToken"] = page_token
        url = "https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params)
        data = drive_rest_json(url)
        files.extend(data.get("files") or [])
        page_token = data.get("nextPageToken") or ""
        if not page_token:
            break
    return files


def drive_rest_find_by_name(name: str) -> str:
    safe_name = name.replace("'", "\\'")
    params = {"q": f"trashed = false and name = '{safe_name}'", "fields": "files(id)", "pageSize": "1"}
    data = drive_rest_json("https://www.googleapis.com/drive/v3/files?" + urllib.parse.urlencode(params))
    files = data.get("files") or []
    return files[0]["id"] if files else ""


def strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def extract_plain_text_from_xhtml(raw_bytes: bytes) -> str:
    try:
        text = raw_bytes.decode("utf-8", errors="replace")
    except Exception:
        return ""
    # Remove style and script blocks
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    # Replace block tags with newlines
    text = re.sub(r"</?(p|div|h[1-6]|li|tr|blockquote|section|article|br|hr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Unescape common entities
    import html
    text = html.unescape(text)
    # Collapse excessive whitespace
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def parse_epub_structure(epub_path: Path, extract_dir: Path) -> dict:
    """
    Extracts the EPUB zip to extract_dir and parses container.xml, OPF metadata,
    manifest, spine, and table of contents (EPUB3 nav or EPUB2 NCX).
    """
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(epub_path, "r") as zf:
        zf.extractall(extract_dir)

    container_xml = extract_dir / "META-INF" / "container.xml"
    opf_rel_path = None
    if container_xml.exists():
        tree = ET.parse(container_xml)
        for elem in tree.iter():
            if strip_ns(elem.tag) == "rootfile":
                opf_rel_path = elem.attrib.get("full-path")
                if opf_rel_path:
                    break

    if not opf_rel_path:
        # Fallback: find any .opf file
        opf_files = list(extract_dir.rglob("*.opf"))
        if not opf_files:
            raise ValueError("Could not find package.opf in EPUB archive")
        opf_rel_path = str(opf_files[0].relative_to(extract_dir))

    opf_path = extract_dir / opf_rel_path
    opf_dir = posixpath.dirname(opf_rel_path.replace("\\", "/"))

    def resolve_href(href: str) -> str:
        if not href:
            return ""
        clean = href.split("#")[0]
        if opf_dir:
            return posixpath.normpath(posixpath.join(opf_dir, clean))
        return posixpath.normpath(clean)

    tree = ET.parse(opf_path)
    root = tree.getroot()

    title = ""
    subtitle = ""
    creators = []
    publisher = ""
    identifier = ""
    language = "en"
    cover_meta_id = ""

    manifest = {}
    spine_ids = []
    nav_href = ""
    ncx_href = ""
    cover_href = ""

    for child in root:
        cname = strip_ns(child.tag)
        if cname == "metadata":
            for m in child:
                mname = strip_ns(m.tag)
                text_val = (m.text or "").strip()
                if mname == "title" and text_val:
                    if not title:
                        title = text_val
                    elif not subtitle:
                        subtitle = text_val
                elif mname == "creator" and text_val:
                    creators.append(text_val)
                elif mname == "publisher" and text_val and not publisher:
                    publisher = text_val
                elif mname == "identifier" and text_val and not identifier:
                    identifier = text_val
                elif mname == "language" and text_val:
                    language = text_val
                elif mname == "meta":
                    if m.attrib.get("name") == "cover" and m.attrib.get("content"):
                        cover_meta_id = m.attrib.get("content")
        elif cname == "manifest":
            for item in child:
                if strip_ns(item.tag) == "item":
                    item_id = item.attrib.get("id", "")
                    href = item.attrib.get("href", "")
                    media_type = item.attrib.get("media-type", "")
                    props = item.attrib.get("properties", "").split()
                    full_href = resolve_href(href)
                    manifest[item_id] = {
                        "id": item_id,
                        "href": href,
                        "full_href": full_href,
                        "media_type": media_type,
                        "properties": props,
                    }
                    if "nav" in props and not nav_href:
                        nav_href = full_href
                    if media_type == "application/x-dtbncx+xml" and not ncx_href:
                        ncx_href = full_href
                    if "cover-image" in props and not cover_href:
                        cover_href = full_href
        elif cname == "spine":
            toc_attr = child.attrib.get("toc", "")
            if toc_attr and toc_attr in manifest and not ncx_href:
                ncx_href = manifest[toc_attr]["full_href"]
            for itemref in child:
                if strip_ns(itemref.tag) == "itemref":
                    idref = itemref.attrib.get("idref", "")
                    if idref in manifest:
                        spine_ids.append(idref)

    if not cover_href and cover_meta_id and cover_meta_id in manifest:
        cover_href = manifest[cover_meta_id]["full_href"]

    if not cover_href:
        for item in manifest.values():
            if item["media_type"].startswith("image/") and "cover" in item["href"].lower():
                cover_href = item["full_href"]
                break

    # Parse TOC mapping: full_href -> label, plus ordered TOC list
    toc_entries = []
    href_to_title = {}

    if nav_href and (extract_dir / nav_href).exists():
        try:
            nav_dir = posixpath.dirname(nav_href)
            nav_tree = ET.parse(extract_dir / nav_href)
            for nav_el in nav_tree.iter():
                if strip_ns(nav_el.tag) == "nav":
                    # Check if toc nav
                    attrib_str = " ".join(f"{k}={v}" for k, v in nav_el.attrib.items()).lower()
                    if "toc" in attrib_str or not toc_entries:
                        for a_el in nav_el.iter():
                            if strip_ns(a_el.tag) == "a" and a_el.attrib.get("href"):
                                raw_href = a_el.attrib["href"]
                                label = "".join(a_el.itertext()).strip()
                                label = re.sub(r"\s+", " ", label)
                                base_part = raw_href.split("#")[0]
                                frag = raw_href.split("#")[1] if "#" in raw_href else ""
                                resolved = posixpath.normpath(posixpath.join(nav_dir, base_part)) if base_part else nav_href
                                if label:
                                    toc_entries.append({"label": label, "href": resolved, "fragment": frag})
                                    if resolved not in href_to_title:
                                        href_to_title[resolved] = label
                        if toc_entries:
                            break
        except Exception as e:
            print(f"Warning parsing nav_href: {e}", file=sys.stderr)

    if not toc_entries and ncx_href and (extract_dir / ncx_href).exists():
        try:
            ncx_dir = posixpath.dirname(ncx_href)
            ncx_tree = ET.parse(extract_dir / ncx_href)
            for np in ncx_tree.iter():
                if strip_ns(np.tag) == "navPoint":
                    label = ""
                    src = ""
                    for sub in np:
                        sname = strip_ns(sub.tag)
                        if sname == "navLabel":
                            label = "".join(sub.itertext()).strip()
                            label = re.sub(r"\s+", " ", label)
                        elif sname == "content":
                            src = sub.attrib.get("src", "")
                    if label and src:
                        base_part = src.split("#")[0]
                        frag = src.split("#")[1] if "#" in src else ""
                        resolved = posixpath.normpath(posixpath.join(ncx_dir, base_part))
                        toc_entries.append({"label": label, "href": resolved, "fragment": frag})
                        if resolved not in href_to_title:
                            href_to_title[resolved] = label
        except Exception as e:
            print(f"Warning parsing ncx_href: {e}", file=sys.stderr)

    chapters = []
    for idx, item_id in enumerate(spine_ids):
        item = manifest[item_id]
        full_href = item["full_href"]
        ch_file = extract_dir / full_href
        ch_title = href_to_title.get(full_href, "")
        word_count = 0
        if ch_file.exists():
            raw_bytes = ch_file.read_bytes()
            plain = extract_plain_text_from_xhtml(raw_bytes)
            word_count = len(plain.split())
            if not ch_title:
                # Try extracting <title> or <h1>/<h2>
                try:
                    txt_html = raw_bytes.decode("utf-8", errors="replace")
                    m_h = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", txt_html, flags=re.DOTALL | re.IGNORECASE)
                    if m_h:
                        t_cand = re.sub(r"<[^>]+>", "", m_h.group(1)).strip()
                        t_cand = re.sub(r"\s+", " ", t_cand)
                        if t_cand:
                            ch_title = t_cand
                    if not ch_title:
                        m_t = re.search(r"<title[^>]*>(.*?)</title>", txt_html, flags=re.DOTALL | re.IGNORECASE)
                        if m_t:
                            t_cand = re.sub(r"<[^>]+>", "", m_t.group(1)).strip()
                            t_cand = re.sub(r"\s+", " ", t_cand)
                            if t_cand:
                                ch_title = t_cand
                except Exception:
                    pass
        if not ch_title:
            ch_title = f"Section {idx + 1}"

        chapters.append(
            {
                "index": idx,
                "id": item_id,
                "href": full_href,
                "title": ch_title,
                "word_count": word_count,
            }
        )

    # Link TOC entries to chapter index
    href_to_idx = {ch["href"]: ch["index"] for ch in chapters}
    for entry in toc_entries:
        entry["chapter_index"] = href_to_idx.get(entry["href"], 0)

    full_title = f"{title}: {subtitle}" if (title and subtitle and subtitle.lower() not in title.lower()) else (title or epub_path.stem)

    return {
        "title": full_title,
        "author": ", ".join(creators) if creators else "Unknown Author",
        "publisher": publisher,
        "language": language,
        "identifier": identifier,
        "opf_dir": opf_dir,
        "cover_href": cover_href,
        "chapters": chapters,
        "toc": toc_entries,
    }


IGNORED_FOLDER_IDS = {
    "1jN3X4CNW5vpvNyu47TR32kUJFPmTZRba",  # ElevenReader epubs
    "1pY3dK-t0C9_5v9hcIuzeyCNLN3Oog6oM",  # tts-epub converter2/output_epubs
    "18ilVVqJHmQa5y-96tCN40uY5uCP7Oj2W",  # tts-epub converter2
}
IGNORED_FOLDER_NAMES = {"elevenreader epubs", "elevenreader", "epub_out", "output_epubs", "tts-epub"}


def is_ignored_epub(item_or_book: dict, extra_ignored_folder_ids: set = None) -> bool:
    parent_id = (item_or_book.get("parent_folder_id") or "").strip()
    if not parent_id and item_or_book.get("parents"):
        parent_id = item_or_book["parents"][0]
    if parent_id in IGNORED_FOLDER_IDS or (extra_ignored_folder_ids and parent_id in extra_ignored_folder_ids):
        return True
    folder_path = (item_or_book.get("folder_path") or "").lower().strip()
    for ignored_name in IGNORED_FOLDER_NAMES:
        if ignored_name in folder_path:
            return True
    name = (item_or_book.get("name") or "").lower().strip()
    title = (item_or_book.get("title") or "").lower().strip()
    if (
        name == "book.epub"
        or "getting started with adobe digital editions" in name
        or "getting started with adobe digital editions" in title
        or "elevenreader" in name
        or "(tts)" in name
        or "-tts.epub" in name
        or "_tts.epub" in name
    ):
        return True
    return False


def sync_library_from_drive() -> list:
    """Paginates across all pages of `gdrive readonly search --name-contains .epub --max 100 --json` (or falls back to drive_epubs.json) and updates SQLite."""
    cache_json_path = DATA_DIR / "drive_epubs.json"
    folder_map_path = DATA_DIR / "folder_map.json"
    ignored_ids_path = DATA_DIR / "ignored_file_ids.json"
    folder_map = {}
    if folder_map_path.exists():
        try:
            folder_map = json.loads(folder_map_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    ignored_file_ids = set()
    if ignored_ids_path.exists():
        try:
            ignored_file_ids = set(json.loads(ignored_ids_path.read_text(encoding="utf-8")))
        except Exception:
            pass

    # Refresh ignored folder file list via Drive REST API or local gdrive
    dynamic_ignored_folders = set(IGNORED_FOLDER_IDS)
    if has_drive_oauth():
        try:
            q_folders = (
                "trashed = false and mimeType = 'application/vnd.google-apps.folder' and "
                "(name = 'ElevenReader epubs' or name = 'epub_out' or name = 'output_epubs' "
                "or name = 'tts-epub converter' or name = 'tts-epub converter2')"
            )
            f_data = drive_rest_json(
                "https://www.googleapis.com/drive/v3/files?"
                + urllib.parse.urlencode({"q": q_folders, "fields": "files(id,name)", "pageSize": "100"})
            )
            for f_item in f_data.get("files") or []:
                if f_item.get("id"):
                    dynamic_ignored_folders.add(f_item["id"])
        except Exception:
            pass
    elif os.path.exists(GDRIVE_BIN):
        for ig_folder_id in IGNORED_FOLDER_IDS:
            ls_proc = run_gdrive(["readonly", "ls", ig_folder_id, "--json"], timeout=30)
            if ls_proc.returncode == 0 and ls_proc.stdout.strip():
                try:
                    for f_item in json.loads(ls_proc.stdout):
                        if f_item.get("id"):
                            ignored_file_ids.add(f_item["id"])
                    ignored_ids_path.write_text(json.dumps(sorted(ignored_file_ids)), encoding="utf-8")
                except Exception:
                    pass

    all_fetched = {}
    cursor_before = None
    gdrive_ok = False
    if has_drive_oauth():
        for item in drive_rest_search_epubs():
            all_fetched[item["id"]] = item
        gdrive_ok = True
    elif not os.path.exists(GDRIVE_BIN):
        raise RuntimeError("Google Drive OAuth is not connected. Open Settings -> Connect Google Drive via OAuth.")

    while not gdrive_ok:
        cmd = ["readonly", "search", "--name-contains", ".epub", "--max", "100", "--json"]
        if cursor_before:
            cmd.extend(["--modified-before", cursor_before.replace("Z", "")])
        proc = run_gdrive(cmd, timeout=45)
        if proc.returncode != 0 or not proc.stdout.strip():
            break
        gdrive_ok = True
        batch = json.loads(proc.stdout)
        if not batch:
            break
        new_count = 0
        for item in batch:
            if item["id"] not in all_fetched:
                all_fetched[item["id"]] = item
                new_count += 1
        if len(batch) < 100 or new_count == 0:
            break
        cursor_before = batch[-1].get("modifiedTime")

    if gdrive_ok and all_fetched:
        items = list(all_fetched.values())
        for it in items:
            if not it.get("folder_path") and it.get("name") in folder_map:
                it["folder_path"] = folder_map[it["name"]]
        try:
            cache_json_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    elif cache_json_path.exists():
        items = json.loads(cache_json_path.read_text(encoding="utf-8"))
    else:
        raise RuntimeError("Could not load EPUB catalog from Google Drive")

    conn = get_db()
    try:
        with conn:
            if ignored_file_ids:
                placeholders = ",".join("?" for _ in ignored_file_ids)
                conn.execute(f"DELETE FROM books WHERE id IN ({placeholders})", tuple(ignored_file_ids))

            for item in items:
                mime = item.get("mimeType", "")
                name = item.get("name", "")
                if mime != "application/epub+zip" and not name.lower().endswith(".epub"):
                    continue
                file_id = item["id"]
                if file_id in ignored_file_ids:
                    continue
                size = int(item.get("size", 0) or 0)
                mod_time = item.get("modifiedTime", "")
                web_link = item.get("webViewLink", "")
                parents = item.get("parents") or []
                parent_id = parents[0] if parents else ""
                folder_path = item.get("folder_path") or folder_map.get(name, "")
                if is_ignored_epub({"parent_folder_id": parent_id, "folder_path": folder_path, "name": name}, dynamic_ignored_folders):
                    conn.execute("DELETE FROM books WHERE id = ?", (file_id,))
                    continue

                existing = conn.execute("SELECT id, title, downloaded FROM books WHERE id = ?", (file_id,)).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE books
                        SET name = ?, size = ?, modified_time = ?, web_view_link = ?,
                            parent_folder_id = COALESCE(NULLIF(?, ''), parent_folder_id),
                            folder_path = COALESCE(NULLIF(?, ''), folder_path)
                        WHERE id = ?
                        """,
                        (name, size, mod_time, web_link, parent_id, folder_path, file_id),
                    )
                else:
                    clean_title = re.sub(r"\.epub$", "", name, flags=re.IGNORECASE)
                    clean_title = re.sub(r"_for_geoffrey[-_]gordon$", "", clean_title, flags=re.IGNORECASE)
                    clean_title = re.sub(r"_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}$", "", clean_title)
                    clean_title = clean_title.replace("_", " ").replace("-", " ")
                    clean_title = " ".join(w[:1].upper() + w[1:] for w in clean_title.split())
                    conn.execute(
                        """
                        INSERT INTO books (id, name, title, author, size, modified_time, web_view_link, parent_folder_id, folder_path, downloaded)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                        """,
                        (file_id, name, clean_title, "", size, mod_time, web_link, parent_id, folder_path),
                    )
        # Extract official OPF metadata for any newly added books missing author (up to 15 per sync call)
        unmeta_rows = conn.execute("SELECT id FROM books WHERE (author = '' OR author IS NULL) LIMIT 15").fetchall()
        for ur in unmeta_rows:
            bid = ur["id"]
            tmp_epub = Path(tempfile.gettempdir()) / f"sync_meta_{bid}.epub"
            downloaded_ok = False
            if has_drive_oauth():
                try:
                    drive_rest_download(bid, tmp_epub)
                    downloaded_ok = tmp_epub.exists()
                except Exception:
                    downloaded_ok = False
            elif os.path.exists(GDRIVE_BIN):
                dl_p = run_gdrive(["readonly", "download", bid, "--out", str(tmp_epub)], timeout=30)
                downloaded_ok = dl_p.returncode == 0 and tmp_epub.exists()
            if downloaded_ok:
                try:
                    import extract_meta_helper
                    t_val, a_val, p_val = extract_meta_helper.extract_opf_metadata(str(tmp_epub))
                    with conn:
                        conn.execute(
                            "UPDATE books SET title = COALESCE(NULLIF(?, ''), title), author = COALESCE(NULLIF(?, ''), author), publisher = COALESCE(NULLIF(?, ''), publisher) WHERE id = ?",
                            (t_val, a_val, p_val, bid),
                        )
                except Exception:
                    pass
                finally:
                    try:
                        tmp_epub.unlink()
                    except Exception:
                        pass
        return get_all_books()
    finally:
        conn.close()


def deduplicate_books(books: list) -> list:
    """Keeps one canonical entry per book, preferring annotated/cached/foldered copies."""
    def _norm_title(s: str) -> str:
        s = re.sub(r"\.epub$", "", (s or "").lower())
        s = re.sub(r"_for_geoffrey[-_]gordon", "", s)
        return re.sub(r"[^a-z0-9]+", " ", s).strip()

    def _norm_author(s: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", (s or "").lower())

    def _score(b: dict) -> tuple:
        return (
            1 if (b.get("annotation_count") or 0) > 0 else 0,
            1 if b.get("downloaded") else 0,
            1 if b.get("last_opened_at") else 0,
            1 if (b.get("folder_path") or "").strip() else 0,
            1 if (b.get("author") or "").strip() else 0,
            b.get("size") or 0,
            b.get("modified_time") or "",
        )

    groups: dict[str, list] = {}
    for b in books:
        k = _norm_title(b.get("title") or b.get("name") or "") or b["id"]
        groups.setdefault(k, []).append(b)

    keep_ids = set()
    for items in groups.values():
        if len(items) == 1:
            keep_ids.add(items[0]["id"])
            continue
        reps: list[dict] = []
        for it in sorted(items, key=_score, reverse=True):
            if (it.get("annotation_count") or 0) > 0:
                keep_ids.add(it["id"])
                reps.append(it)
                continue
            a = _norm_author(it.get("author") or "")
            matched = False
            for rep in reps:
                ra = _norm_author(rep.get("author") or "")
                if not a or not ra or a == ra or a in ra or ra in a:
                    matched = True
                    break
            if not matched:
                keep_ids.add(it["id"])
                reps.append(it)

    return [b for b in books if b["id"] in keep_ids]


def get_all_books() -> list:
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT b.*,
                   (SELECT COUNT(*) FROM annotations a WHERE a.book_id = b.id) AS annotation_count
            FROM books b
            ORDER BY
                CASE WHEN b.last_opened_at IS NOT NULL THEN 0 ELSE 1 END,
                b.last_opened_at DESC,
                b.modified_time DESC
            """
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            if is_ignored_epub(d):
                continue
            if d.get("manifest_json"):
                try:
                    d["manifest"] = json.loads(d["manifest_json"])
                except Exception:
                    d["manifest"] = None
            else:
                d["manifest"] = None
            d.pop("manifest_json", None)
            raw_tags = d.get("tags") or "[]"
            try:
                d["tags"] = json.loads(raw_tags) if isinstance(raw_tags, str) else list(raw_tags)
            except Exception:
                d["tags"] = []
            result.append(d)
        return deduplicate_books(result)
    finally:
        conn.close()


def ensure_book_downloaded_and_parsed(book_id: str) -> dict:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not row:
            raise ValueError(f"Book {book_id} not found in library")
        book = dict(row)
    finally:
        conn.close()

    book_folder = BOOKS_DIR / book_id
    epub_file = book_folder / "book.epub"
    extract_dir = book_folder / "extracted"

    if not epub_file.exists() or not extract_dir.exists() or not book.get("manifest_json"):
        book_folder.mkdir(parents=True, exist_ok=True)
        if not epub_file.exists():
            if has_drive_oauth():
                drive_rest_download(book_id, epub_file)
            else:
                proc = run_gdrive(["readonly", "download", book_id, "--out", str(epub_file)], timeout=120)
                if proc.returncode != 0:
                    raise RuntimeError(f"Failed to download EPUB from Google Drive: {proc.stderr.strip()}")

        # Also fetch parent folder ID if missing
        parent_folder_id = book.get("parent_folder_id") or ""
        if not parent_folder_id and not has_drive_oauth() and os.path.exists(GDRIVE_BIN):
            info_proc = run_gdrive(["readonly", "info", book_id, "--json"], timeout=30)
            if info_proc.returncode == 0:
                try:
                    info_data = json.loads(info_proc.stdout)
                    parents = info_data.get("parents") or []
                    if parents:
                        parent_folder_id = parents[0]
                except Exception:
                    pass

        manifest = parse_epub_structure(epub_file, extract_dir)
        now_iso = datetime.now(timezone.utc).isoformat()

        conn = get_db()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE books
                    SET title = ?, author = ?, publisher = ?, cover_href = ?, opf_dir = ?,
                        parent_folder_id = COALESCE(NULLIF(?, ''), parent_folder_id),
                        downloaded = 1, last_opened_at = ?, manifest_json = ?
                    WHERE id = ?
                    """,
                    (
                        manifest["title"],
                        manifest["author"],
                        manifest["publisher"],
                        manifest["cover_href"],
                        manifest["opf_dir"],
                        parent_folder_id,
                        now_iso,
                        json.dumps(manifest),
                        book_id,
                    ),
                )
        finally:
            conn.close()

        # Check if there is an existing companion annotations JSON on Google Drive if we have no local annotations
        try_import_annotations_from_drive(book_id, book["name"])
    else:
        now_iso = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        try:
            with conn:
                conn.execute("UPDATE books SET last_opened_at = ? WHERE id = ?", (now_iso, book_id))
        finally:
            conn.close()

    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        d = dict(row)
        d["manifest"] = json.loads(d["manifest_json"]) if d.get("manifest_json") else None
        d.pop("manifest_json", None)
        return d
    finally:
        conn.close()


def companion_filename_for(epub_name: str, ext: str = ".annotations.json") -> str:
    stem = re.sub(r"\.epub$", "", epub_name, flags=re.IGNORECASE)
    return f"{stem}{ext}"


def try_import_annotations_from_drive(book_id: str, epub_name: str):
    """If local DB has 0 annotations for this book, check if a companion .annotations.json exists on Drive."""
    conn = get_db()
    try:
        count = conn.execute("SELECT COUNT(*) AS c FROM annotations WHERE book_id = ?", (book_id,)).fetchone()["c"]
        if count > 0:
            return
    finally:
        conn.close()

    comp_name = companion_filename_for(epub_name, ".annotations.json")
    try:
        comp_id = ""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
            tmp_path = tf.name
        downloaded_ok = False
        if has_drive_oauth():
            comp_id = drive_rest_find_by_name(comp_name)
            if not comp_id:
                return
            drive_rest_download(comp_id, Path(tmp_path))
            downloaded_ok = os.path.exists(tmp_path)
        elif os.path.exists(GDRIVE_BIN):
            proc = run_gdrive(["readonly", "search", "--name-exact", comp_name, "--json"], timeout=30)
            if proc.returncode != 0:
                return
            files = json.loads(proc.stdout or "[]")
            if not files:
                return
            comp_id = files[0]["id"]
            dl = run_gdrive(["readonly", "download", comp_id, "--out", tmp_path], timeout=30)
            downloaded_ok = dl.returncode == 0 and os.path.exists(tmp_path)
        else:
            return

        if downloaded_ok:
            data = json.loads(Path(tmp_path).read_text(encoding="utf-8"))
            anns = data.get("annotations", [])
            conn = get_db()
            with conn:
                conn.execute("UPDATE books SET annotations_drive_id = ? WHERE id = ?", (comp_id, book_id))
                for a in anns:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO annotations
                        (id, book_id, chapter_index, chapter_href, chapter_title, selected_text, note, gemini_response, color, occurrence_index, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            a["id"],
                            book_id,
                            a.get("chapter_index", 0),
                            a.get("chapter_href", ""),
                            a.get("chapter_title", ""),
                            a.get("selected_text", ""),
                            a.get("note", ""),
                            a.get("gemini_response", ""),
                            a.get("color", "yellow"),
                            a.get("occurrence_index", 0),
                            a.get("created_at", datetime.now(timezone.utc).isoformat()),
                            a.get("updated_at", datetime.now(timezone.utc).isoformat()),
                        ),
                    )
            conn.close()
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
    except Exception as e:
        print(f"Optional Drive annotation import notice: {e}", file=sys.stderr)


HIGHLIGHT_SCHEMA = {
    "blue": {
        "label": "Critical Points",
        "short": "Critical Point",
        "description": "Key interventions or important components of the overall argument.",
    },
    "yellow": {
        "label": "Significant Points",
        "short": "Significant Point",
        "description": "Usually the main idea of the paragraph.",
    },
    "red": {
        "label": "Critiques of Scholarship",
        "short": "Critique",
        "description": "The author's critiques of other scholarship.",
    },
    "green": {
        "label": "Definitions & Operationalization",
        "short": "Definition",
        "description": "Key terms or operationalization of key variables.",
    },
    "grey": {
        "label": "Concerns & Questions",
        "short": "Concern",
        "description": "Passages that I either disagree with or find confusing.",
    },
}


def format_annotations_markdown(book: dict, annotations: list) -> str:
    lines = [
        f"# Reading Notes & Color-Coded Dossier: {book.get('title') or book.get('name')}",
        f"**Author:** {book.get('author') or 'Unknown'}  ",
        f"**Exported:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  ",
        "",
    ]

    saved_report = get_setting(f"report_{book.get('id', '')}", "").strip()
    if saved_report:
        lines.extend([
            "## Synthesized Reading Report",
            "",
            saved_report,
            "",
            "---",
            "",
        ])

    lines.extend([
        "## Highlights Organized by Analytical Category",
        "",
    ])

    for color_key, meta in HIGHLIGHT_SCHEMA.items():
        cat_items = [a for a in annotations if (a.get("color") or "yellow") == color_key]
        if not cat_items:
            continue
        lines.append(f"### [{color_key.upper()}] {meta['label']} ({len(cat_items)})")
        lines.append(f"*{meta['description']}*")
        lines.append("")
        for item in cat_items:
            ch_title = item.get("chapter_title") or f"Section {item.get('chapter_index', 0) + 1}"
            pg_str = f", {item['page_number']}" if item.get("page_number") else ""
            lines.append(f"- **[{ch_title}{pg_str}]** \"{item.get('selected_text', '').strip()}\"")
            if item.get("note"):
                lines.append(f"  - **Reader Note:** {item['note'].strip()}")
            if item.get("gemini_response"):
                lines.append(f"  - **Gemini Insight:** {item['gemini_response'].strip()}")
        lines.append("")

    lines.extend([
        "---",
        "",
        "## Chronological Highlights by Chapter",
        "",
    ])

    by_chapter = {}
    for a in annotations:
        ch_title = a.get("chapter_title") or f"Chapter {a.get('chapter_index', 0) + 1}"
        by_chapter.setdefault(ch_title, []).append(a)

    for ch_title, items in by_chapter.items():
        lines.append(f"### {ch_title}")
        lines.append("")
        for item in items:
            c_key = item.get("color", "yellow").lower()
            c_meta = HIGHLIGHT_SCHEMA.get(c_key, {"short": c_key.upper()})
            pg_badge = f" ({item['page_number']})" if item.get("page_number") else ""
            lines.append(f"> **[{c_key.upper()} — {c_meta['short']}{pg_badge}]** \"{item.get('selected_text', '').strip()}\"")
            lines.append("")
            if item.get("note"):
                lines.append(f"**Note:** {item['note'].strip()}")
                lines.append("")
            if item.get("gemini_response"):
                lines.append(f"**Gemini Insight:**\n{item['gemini_response'].strip()}")
                lines.append("")
            lines.append("---")
            lines.append("")
    return "\n".join(lines)


def sync_annotations_to_drive(book_id: str) -> dict:
    """Uploads or updates companion .annotations.json and .notes.md on Google Drive."""
    conn = get_db()
    try:
        book_row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book_row:
            raise ValueError("Book not found")
        book = dict(book_row)
        ann_rows = conn.execute(
            "SELECT * FROM annotations WHERE book_id = ? ORDER BY chapter_index ASC, created_at ASC",
            (book_id,),
        ).fetchall()
        annotations = [dict(r) for r in ann_rows]
    finally:
        conn.close()

    parent_id = book.get("parent_folder_id") or ""
    if not parent_id and not has_drive_oauth() and os.path.exists(GDRIVE_BIN):
        info_proc = run_gdrive(["readonly", "info", book_id, "--json"], timeout=30)
        if info_proc.returncode == 0:
            try:
                info_data = json.loads(info_proc.stdout)
                parents = info_data.get("parents") or []
                if parents:
                    parent_id = parents[0]
            except Exception:
                pass

    json_filename = companion_filename_for(book["name"], ".annotations.json")
    md_filename = companion_filename_for(book["name"], " - Notes.md")

    payload = {
        "book_id": book_id,
        "book_name": book["name"],
        "title": book.get("title"),
        "author": book.get("author"),
        "last_chapter_index": book.get("last_chapter_index", 0),
        "scroll_percent": book.get("scroll_percent", 0.0),
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "annotations": annotations,
    }

    tmp_dir = Path(tempfile.mkdtemp(prefix="epub_sync_"))
    try:
        json_bytes = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
        md_bytes = format_annotations_markdown(book, annotations).encode("utf-8")

        if has_drive_oauth():
            drive_json_id = book.get("annotations_drive_id") or drive_rest_find_by_name(json_filename)
            up_res = drive_rest_upload_file(
                json_filename, json_bytes, "application/json", parent_id=parent_id, existing_id=drive_json_id
            )
            drive_json_id = up_res.get("id", drive_json_id)
            md_id = drive_rest_find_by_name(md_filename)
            drive_rest_upload_file(
                md_filename, md_bytes, "text/markdown", parent_id=parent_id, existing_id=md_id
            )
            now_iso = datetime.now(timezone.utc).isoformat()
            conn = get_db()
            try:
                with conn:
                    conn.execute(
                        "UPDATE books SET annotations_drive_id = ?, last_synced_at = ? WHERE id = ?",
                        (drive_json_id, now_iso, book_id),
                    )
            finally:
                conn.close()
            return {
                "status": "synced",
                "synced_via": "Google Drive OAuth",
                "annotations_drive_id": drive_json_id,
                "json_filename": json_filename,
                "md_filename": md_filename,
                "count": len(annotations),
            }

        json_path = tmp_dir / json_filename
        json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

        md_path = tmp_dir / md_filename
        md_path.write_text(format_annotations_markdown(book, annotations), encoding="utf-8")

        # 1. Check if json file already exists in Drive
        drive_json_id = book.get("annotations_drive_id") or ""
        if not drive_json_id:
            search_proc = run_gdrive(["readonly", "search", "--name-exact", json_filename, "--json"], timeout=30)
            if search_proc.returncode == 0:
                found = json.loads(search_proc.stdout or "[]")
                if found:
                    drive_json_id = found[0]["id"]

        if drive_json_id:
            up_proc = run_gdrive(
                ["mutate", "update", drive_json_id, str(json_path), "--mime-type", "application/json", "--json"],
                timeout=60,
            )
            if up_proc.returncode != 0:
                # Fallback to upload if update failed (e.g. file was deleted)
                drive_json_id = ""

        if not drive_json_id:
            cmd = ["mutate", "upload", str(json_path), "--mime-type", "application/json", "--json"]
            if parent_id:
                cmd.extend(["--parent", parent_id])
            up_proc = run_gdrive(cmd, timeout=60)
            if up_proc.returncode != 0:
                err_msg = up_proc.stderr.strip() or up_proc.stdout.strip()
                # Fallback to local Google Drive for Desktop mount when gdrive CLI lacks standalone credentials
                if "no credentials" in err_msg.lower() or "auth login" in err_msg.lower():
                    my_drive = None
                    cloud_dir = Path.home() / "Library" / "CloudStorage"
                    if cloud_dir.exists():
                        for gd in sorted(cloud_dir.glob("GoogleDrive-*")):
                            cand = gd / "My Drive"
                            if cand.is_dir():
                                my_drive = cand
                                break
                    target_dir = None
                    if my_drive:
                        folder_rel = (book.get("folder_path") or "").strip("/")
                        if folder_rel and (my_drive / folder_rel).is_dir():
                            target_dir = my_drive / folder_rel
                        else:
                            target_dir = my_drive / "Folio Reading Notes"
                    else:
                        target_dir = BASE_DIR / "synced_notes"
                    target_dir.mkdir(parents=True, exist_ok=True)
                    (target_dir / json_filename).write_text(json_path.read_text(encoding="utf-8"), encoding="utf-8")
                    (target_dir / md_filename).write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")

                    now_iso = datetime.now(timezone.utc).isoformat()
                    conn = get_db()
                    try:
                        with conn:
                            conn.execute(
                                "UPDATE books SET last_synced_at = ? WHERE id = ?",
                                (now_iso, book_id),
                            )
                    finally:
                        conn.close()

                    return {
                        "status": "synced",
                        "synced_via": "Google Drive for Desktop",
                        "local_folder": str(target_dir),
                        "json_filename": json_filename,
                        "md_filename": md_filename,
                        "count": len(annotations),
                    }
                raise RuntimeError(f"Drive upload failed: {err_msg}")
            try:
                up_data = json.loads(up_proc.stdout)
                drive_json_id = up_data.get("id", "")
            except Exception:
                pass

        # 2. Also sync readable Markdown file alongside it
        md_drive_id = ""
        search_md = run_gdrive(["readonly", "search", "--name-exact", md_filename, "--json"], timeout=30)
        if search_md.returncode == 0:
            found_md = json.loads(search_md.stdout or "[]")
            if found_md:
                md_drive_id = found_md[0]["id"]

        if md_drive_id:
            run_gdrive(["mutate", "update", md_drive_id, str(md_path), "--mime-type", "text/markdown"], timeout=60)
        else:
            cmd_md = ["mutate", "upload", str(md_path), "--mime-type", "text/markdown"]
            if parent_id:
                cmd_md.extend(["--parent", parent_id])
            run_gdrive(cmd_md, timeout=60)

        now_iso = datetime.now(timezone.utc).isoformat()
        conn = get_db()
        try:
            with conn:
                conn.execute(
                    "UPDATE books SET annotations_drive_id = ?, last_synced_at = ? WHERE id = ?",
                    (drive_json_id, now_iso, book_id),
                )
        finally:
            conn.close()

        return {
            "status": "synced",
            "annotations_drive_id": drive_json_id,
            "json_filename": json_filename,
            "md_filename": md_filename,
            "synced_at": now_iso,
            "count": len(annotations),
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


_AUTO_SYNC_TIMERS: dict[str, threading.Timer] = {}
_AUTO_SYNC_LOCK = threading.Lock()


def schedule_book_auto_sync(book_id: str, delay_seconds: float = 15.0):
    """Debounces background Drive sync shortly after any annotation create/update/delete."""
    def _run():
        with _AUTO_SYNC_LOCK:
            _AUTO_SYNC_TIMERS.pop(book_id, None)
        try:
            sync_annotations_to_drive(book_id)
        except Exception:
            pass

    with _AUTO_SYNC_LOCK:
        prev = _AUTO_SYNC_TIMERS.get(book_id)
        if prev:
            prev.cancel()
        t = threading.Timer(delay_seconds, _run)
        t.daemon = True
        _AUTO_SYNC_TIMERS[book_id] = t
        t.start()


def sync_dirty_books_to_drive() -> int:
    """Syncs any book whose annotations were updated since its last_synced_at."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT b.id FROM books b
            WHERE EXISTS (
                SELECT 1 FROM annotations a
                WHERE a.book_id = b.id
                  AND (b.last_synced_at IS NULL OR b.last_synced_at = '' OR a.updated_at > b.last_synced_at)
            )
            """
        ).fetchall()
        book_ids = [r["id"] for r in rows]
    finally:
        conn.close()

    synced = 0
    for bid in book_ids:
        try:
            sync_annotations_to_drive(bid)
            synced += 1
        except Exception:
            pass
    return synced


def start_periodic_drive_sync(interval_seconds: int = 900):
    """Runs a background sweep every 15 minutes to sync any unsynced highlights to Drive."""
    def _loop():
        while True:
            time.sleep(interval_seconds)
            try:
                sync_dirty_books_to_drive()
            except Exception:
                pass

    t = threading.Thread(target=_loop, daemon=True, name="folio-periodic-drive-sync")
    t.start()


def get_book_chapter_text(book_id: str, chapter_index: int) -> tuple[str, str]:
    book = ensure_book_downloaded_and_parsed(book_id)
    chapters = (book.get("manifest") or {}).get("chapters") or []
    if not chapters or chapter_index < 0 or chapter_index >= len(chapters):
        return "", ""
    ch = chapters[chapter_index]
    ch_path = BOOKS_DIR / book_id / "extracted" / ch["href"]
    if not ch_path.exists():
        return ch.get("title", ""), ""
    return ch.get("title", ""), extract_plain_text_from_xhtml(ch_path.read_bytes())


def search_book_passages(book_id: str, query: str, max_results: int = 8) -> list:
    book = ensure_book_downloaded_and_parsed(book_id)
    chapters = (book.get("manifest") or {}).get("chapters") or []
    extract_dir = BOOKS_DIR / book_id / "extracted"

    terms = [t.lower() for t in re.findall(r"\w{3,}", query)]
    if not terms:
        terms = [query.lower().strip()]

    results = []
    for ch in chapters:
        ch_path = extract_dir / ch["href"]
        if not ch_path.exists():
            continue
        text = extract_plain_text_from_xhtml(ch_path.read_bytes())
        paragraphs = [p.strip() for p in text.split("\n") if len(p.strip()) > 30]
        for p in paragraphs:
            p_low = p.lower()
            if query.lower().strip() in p_low:
                score = 10 + sum(p_low.count(t) for t in terms)
            else:
                matched_terms = sum(1 for t in terms if t in p_low)
                if matched_terms == 0:
                    continue
                score = matched_terms * 2 + sum(p_low.count(t) for t in terms)
            if score > 0:
                results.append(
                    {
                        "chapter_index": ch["index"],
                        "chapter_title": ch["title"],
                        "snippet": p[:650],
                        "score": score,
                    }
                )

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:max_results]


def format_bibtex(book: dict) -> str:
    """Generates a clean BibTeX @book entry from book metadata."""
    title = (book.get("title") or book.get("name") or "Untitled").strip()
    author = (book.get("author") or "Unknown").strip()
    publisher = (book.get("publisher") or "").strip()
    mod_time = (book.get("modified_time") or "")[:4]
    year = mod_time if mod_time.isdigit() else "n.d."

    first_author = re.sub(r"[^a-z0-9]", "", (author.split(",")[0].split()[-1] if author else "book").lower())
    first_word = re.sub(r"[^a-z0-9]", "", (title.split()[0] if title else "work").lower())
    cite_key = f"{first_author}{year}{first_word}" or "folio_book"

    lines = [
        f"@book{{{cite_key},",
        f"  title = {{{title}}},",
        f"  author = {{{author}}},",
    ]
    if publisher:
        lines.append(f"  publisher = {{{publisher}}},")
    if year != "n.d.":
        lines.append(f"  year = {{{year}}},")
    if book.get("web_view_link"):
        lines.append(f"  url = {{{book['web_view_link']}}},")
    lines.append("}")
    return "\n".join(lines) + "\n"


def search_all_books_and_notes(query: str, max_results: int = 30) -> dict:
    """Searches saved annotations across all books and full text of downloaded EPUBs."""
    q = query.strip()
    if not q:
        return {"annotation_matches": [], "book_matches": []}

    like_q = f"%{q}%"
    conn = get_db()
    try:
        ann_rows = conn.execute(
            """
            SELECT a.*, b.title AS book_title, b.author AS book_author, b.name AS book_name
            FROM annotations a
            JOIN books b ON a.book_id = b.id
            WHERE a.selected_text LIKE ? OR a.note LIKE ? OR a.gemini_response LIKE ?
            ORDER BY a.updated_at DESC
            LIMIT ?
            """,
            (like_q, like_q, like_q, max_results),
        ).fetchall()
        downloaded_books = conn.execute(
            "SELECT id, title, author, name FROM books WHERE downloaded = 1 ORDER BY last_opened_at DESC"
        ).fetchall()
    finally:
        conn.close()

    annotation_matches = [dict(r) for r in ann_rows]
    book_matches = []
    for b in downloaded_books:
        hits = search_book_passages(b["id"], q, max_results=3)
        for h in hits:
            book_matches.append(
                {
                    "book_id": b["id"],
                    "book_title": b["title"] or b["name"],
                    "book_author": b["author"] or "",
                    "chapter_index": h["chapter_index"],
                    "chapter_title": h["chapter_title"],
                    "snippet": h["snippet"],
                    "score": h["score"],
                }
            )
    book_matches.sort(key=lambda x: x["score"], reverse=True)
    return {
        "annotation_matches": annotation_matches,
        "book_matches": book_matches[:max_results],
    }


def export_notes_to_gdoc(book_id: str) -> dict:
    """Exports book highlights & notes as a Google Doc in Google Drive via gdrive CLI."""
    conn = get_db()
    try:
        book_row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if not book_row:
            raise RuntimeError("Book not found")
        ann_rows = conn.execute(
            "SELECT * FROM annotations WHERE book_id = ? ORDER BY chapter_index ASC, created_at ASC",
            (book_id,),
        ).fetchall()
    finally:
        conn.close()

    book = dict(book_row)
    md_text = format_annotations_markdown(book, [dict(r) for r in ann_rows])
    doc_name = companion_filename_for(book["name"], " — Reading Notes")
    if has_drive_oauth():
        return drive_rest_upload_file(
            doc_name,
            md_text.encode("utf-8"),
            "text/markdown",
            parent_id=book.get("parent_folder_id") or "",
            target_mime="application/vnd.google-apps.document",
        )
    doc_name_md = doc_name + ".md"
    tmp_dir = Path(tempfile.gettempdir())
    tmp_path = tmp_dir / doc_name_md
    tmp_path.write_text(md_text, encoding="utf-8")

    cmd = [
        "mutate",
        "upload",
        str(tmp_path),
        "--mime-type",
        "application/vnd.google-apps.document",
        "--json",
    ]
    if book.get("parent_folder_id"):
        cmd.extend(["--parent", book["parent_folder_id"]])

    proc = run_gdrive(cmd, timeout=45)
    try:
        tmp_path.unlink(missing_ok=True)
    except Exception:
        pass
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "Failed to upload Google Doc")
    return json.loads(proc.stdout)


def call_gemini_interactions_api(
    prompt: str,
    system_instruction: str = "",
    model: str = "gemini-4-hydrogen",
    previous_interaction_id: str = "",
) -> dict:
    """
    Calls the Gemini Interactions API (`POST https://generativelanguage.googleapis.com/v1/interactions`)
    using the current Gemini 4 models (`gemini-4-hydrogen`, `gemini-4-argon`, `gemini-4-carbon`, `gemini-3.5-flash-lite`)
    with automatic fallback on 503 high-demand spikes.
    """
    api_key = resolve_gemini_api_key()
    if not api_key:
        raise RuntimeError("MISSING_API_KEY")

    primary_model = model or "gemini-4-hydrogen"
    fallback_order = [primary_model]
    for m_cand in ("gemini-4-argon", "gemini-3.5-flash-lite", "gemini-4-hydrogen", "gemini-4-carbon"):
        if m_cand not in fallback_order:
            fallback_order.append(m_cand)

    last_err = None
    used_model = primary_model
    resp_json = None

    for candidate_model in fallback_order:
        body = {
            "model": candidate_model,
            "input": prompt,
        }
        if system_instruction:
            body["system_instruction"] = system_instruction
        if previous_interaction_id:
            body["previous_interaction_id"] = previous_interaction_id

        data_bytes = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            GEMINI_API_BASE,
            data=data_bytes,
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=90) as resp:
                resp_json = json.loads(resp.read().decode("utf-8"))
                used_model = candidate_model
                break
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            last_err = f"Gemini API error ({e.code}): {err_body}"
            if e.code in (429, 500, 502, 503, 504):
                continue
            raise RuntimeError(last_err)

    if resp_json is None:
        raise RuntimeError(last_err or "Gemini API call failed")

    # Extract text from steps (`model_output` -> `content` -> `text`) or `output_text`
    output_text = resp_json.get("output_text") or ""
    if not output_text:
        texts = []
        for step in resp_json.get("steps") or []:
            if step.get("type") == "model_output":
                for part in step.get("content") or []:
                    if part.get("type") == "text" and part.get("text"):
                        texts.append(part["text"])
        output_text = "".join(texts).strip()

    return {
        "id": resp_json.get("id", ""),
        "status": resp_json.get("status", "completed"),
        "output_text": output_text,
        "model": used_model,
        "usage": resp_json.get("usage", {}),
    }


class ReaderRequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Quiet standard logging to keep console clean
        pass

    def send_json(self, data: dict | list, status: int = 200, extra_headers: dict = None):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def send_error_json(self, message: str, status: int = 400, code: str = "ERROR"):
        self.send_json({"error": message, "code": code}, status=status)

    def is_authenticated(self) -> bool:
        passcode = get_active_passcode()
        if not passcode:
            return True
        cookie_hdr = self.headers.get("Cookie", "")
        if not cookie_hdr:
            return False
        c = SimpleCookie()
        c.load(cookie_hdr)
        m = c.get("folio_session")
        return bool(m and verify_session_token(m.value, passcode))

    def require_auth(self) -> bool:
        if self.is_authenticated():
            return True
        self.send_error_json("Authentication required", status=401, code="AUTH_REQUIRED")
        return False

    def _base_url(self) -> str:
        proto = self.headers.get("X-Forwarded-Proto") or "http"
        host = self.headers.get("X-Forwarded-Host") or self.headers.get("Host") or "127.0.0.1:8765"
        return f"{proto}://{host}"

    def read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        try:
            if path == "/api/auth/status":
                self.send_json(
                    {
                        "passcode_required": bool(get_active_passcode()),
                        "authenticated": self.is_authenticated(),
                    }
                )
                return

            if path == "/api/drive/oauth/callback":
                code = (query.get("code") or [""])[0].strip()
                if not code:
                    self.send_error(HTTPStatus.BAD_REQUEST, "Missing OAuth code")
                    return
                cfg = get_drive_oauth_config()
                redirect_uri = f"{self._base_url()}/api/drive/oauth/callback"
                body = urllib.parse.urlencode(
                    {
                        "code": code,
                        "client_id": cfg["client_id"],
                        "client_secret": cfg["client_secret"],
                        "redirect_uri": redirect_uri,
                        "grant_type": "authorization_code",
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    "https://oauth2.googleapis.com/token",
                    data=body,
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    tok_data = json.loads(resp.read().decode("utf-8"))
                if tok_data.get("refresh_token"):
                    set_setting("google_refresh_token", tok_data["refresh_token"])
                _DRIVE_TOKEN_CACHE["access_token"] = tok_data.get("access_token", "")
                _DRIVE_TOKEN_CACHE["expires_at"] = time.time() + float(tok_data.get("expires_in", 3500))
                self.send_response(302)
                self.send_header("Location", "/?drive_connected=1")
                self.end_headers()
                return

            if (path.startswith("/api/") or path.startswith("/epub/")) and not self.require_auth():
                return

            if path == "/api/drive/oauth/start":
                cfg = get_drive_oauth_config()
                if not (cfg["client_id"] and cfg["client_secret"]):
                    self.send_error_json("Set Google Client ID and Client Secret in Settings first.", 400)
                    return
                redirect_uri = f"{self._base_url()}/api/drive/oauth/callback"
                params = {
                    "client_id": cfg["client_id"],
                    "redirect_uri": redirect_uri,
                    "response_type": "code",
                    "scope": "https://www.googleapis.com/auth/drive",
                    "access_type": "offline",
                    "prompt": "consent",
                }
                auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
                self.send_json({"auth_url": auth_url})
                return

            if path == "/api/status":
                api_key = resolve_gemini_api_key()
                model = get_setting("gemini_model", "gemini-4-hydrogen")
                oauth_cfg = get_drive_oauth_config()
                self.send_json(
                    {
                        "ok": True,
                        "gdrive_connected": has_drive_oauth() or os.path.exists(GDRIVE_BIN),
                        "drive_oauth_connected": has_drive_oauth(),
                        "google_client_id": oauth_cfg["client_id"],
                        "passcode_enabled": bool(get_active_passcode()),
                        "gemini_configured": bool(api_key),
                        "gemini_key_masked": (api_key[:4] + "..." + api_key[-4:]) if len(api_key) > 8 else ("Configured" if api_key else ""),
                        "gemini_model": model,
                    }
                )
                return

            if path == "/api/books":
                books = get_all_books()
                if not books:
                    books = sync_library_from_drive()
                self.send_json({"books": books})
                return

            if path == "/api/search":
                q = (query.get("q") or [""])[0].strip()
                self.send_json(search_all_books_and_notes(q))
                return

            m_book = re.match(r"^/api/books/([^/]+)$", path)
            if m_book:
                book_id = urllib.parse.unquote(m_book.group(1))
                book = ensure_book_downloaded_and_parsed(book_id)
                conn = get_db()
                try:
                    anns = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT * FROM annotations WHERE book_id = ? ORDER BY chapter_index ASC, created_at ASC",
                            (book_id,),
                        ).fetchall()
                    ]
                    chats = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT * FROM chats WHERE book_id = ? ORDER BY created_at ASC",
                            (book_id,),
                        ).fetchall()
                    ]
                    bookmarks = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT * FROM bookmarks WHERE book_id = ? ORDER BY chapter_index ASC, scroll_percent ASC, created_at ASC",
                            (book_id,),
                        ).fetchall()
                    ]
                finally:
                    conn.close()
                raw_tags = book.get("tags") or "[]"
                try:
                    book["tags"] = json.loads(raw_tags) if isinstance(raw_tags, str) else list(raw_tags)
                except Exception:
                    book["tags"] = []
                book["annotations"] = anns
                book["chats"] = chats
                book["bookmarks"] = bookmarks
                book["reading_report"] = get_setting(f"report_{book_id}", "")
                self.send_json(book)
                return

            m_search = re.match(r"^/api/books/([^/]+)/search$", path)
            if m_search:
                book_id = urllib.parse.unquote(m_search.group(1))
                q = (query.get("q") or [""])[0].strip()
                if not q:
                    self.send_json({"results": []})
                    return
                results = search_book_passages(book_id, q, max_results=20)
                self.send_json({"results": results})
                return

            m_export = re.match(r"^/api/books/([^/]+)/export\.md$", path)
            if m_export:
                book_id = urllib.parse.unquote(m_export.group(1))
                conn = get_db()
                try:
                    book_row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
                    ann_rows = conn.execute(
                        "SELECT * FROM annotations WHERE book_id = ? ORDER BY chapter_index ASC, created_at ASC",
                        (book_id,),
                    ).fetchall()
                finally:
                    conn.close()
                if not book_row:
                    self.send_error_json("Book not found", 404)
                    return
                md_text = format_annotations_markdown(dict(book_row), [dict(r) for r in ann_rows])
                raw = md_text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{companion_filename_for(book_row["name"], "-notes.md")}"',
                )
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            m_export_bib = re.match(r"^/api/books/([^/]+)/export\.bib$", path)
            if m_export_bib:
                book_id = urllib.parse.unquote(m_export_bib.group(1))
                conn = get_db()
                try:
                    book_row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
                finally:
                    conn.close()
                if not book_row:
                    self.send_error_json("Book not found", 404)
                    return
                bib_text = format_bibtex(dict(book_row))
                raw = bib_text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/x-bibtex; charset=utf-8")
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{companion_filename_for(book_row["name"], ".bib")}"',
                )
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            # Serve extracted EPUB content (/epub/<book_id>/<rel_path>)
            m_epub = re.match(r"^/epub/([^/]+)/(.*)$", path)
            if m_epub:
                book_id = urllib.parse.unquote(m_epub.group(1))
                rel_path = urllib.parse.unquote(m_epub.group(2))
                extract_root = (BOOKS_DIR / book_id / "extracted").resolve()
                target_file = (extract_root / rel_path).resolve()
                if not str(target_file).startswith(str(extract_root)) or not target_file.is_file():
                    self.send_error(HTTPStatus.NOT_FOUND, "EPUB resource not found")
                    return

                ctype, _ = mimetypes.guess_type(str(target_file))
                if not ctype:
                    ctype = "application/octet-stream"
                raw = target_file.read_bytes()
                # Serve .xhtml as text/html when requested in iframe so browser DOM manipulation & selection work seamlessly.
                # Expand self-closing non-void XHTML tags (e.g. <a id="page_3"/>) so HTML5 parser doesn't clone them across blocks.
                if target_file.suffix.lower() in (".xhtml", ".html", ".htm"):
                    ctype = "text/html; charset=utf-8"
                    text = raw.decode("utf-8", errors="replace")
                    text = re.sub(
                        r"<(a|span|div|p|sup|sub|em|i|b|strong|small|label)((?:\s+[^<>\"']*(?:\"[^\"]*\"|'[^']*'))*)\s*/>",
                        lambda m: f"<{m.group(1)}{m.group(2) or ''}></{m.group(1)}>",
                        text,
                        flags=re.IGNORECASE,
                    )
                    raw = text.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                return

            # Serve static frontend files
            if path == "/" or path == "/index.html":
                target = STATIC_DIR / "index.html"
            else:
                rel = path.lstrip("/")
                target = (STATIC_DIR / rel).resolve()
                if not str(target).startswith(str(STATIC_DIR.resolve())):
                    self.send_error(HTTPStatus.FORBIDDEN)
                    return

            if target.is_file():
                ctype, _ = mimetypes.guess_type(str(target))
                raw = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", ctype or "text/plain; charset=utf-8")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(raw)
                return

            self.send_error(HTTPStatus.NOT_FOUND, "File not found")

        except Exception as e:
            traceback.print_exc()
            self.send_error_json(str(e), status=500)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            body = self.read_json_body()

            if path == "/api/auth/login":
                entered = str(body.get("passcode") or "").strip()
                expected = get_active_passcode()
                if expected and not hmac.compare_digest(entered, expected):
                    self.send_error_json("Incorrect passcode", status=401, code="INVALID_PASSCODE")
                    return
                if expected:
                    tok = make_session_token(expected)
                    cookie = f"folio_session={tok}; Path=/; Max-Age={30*86400}; HttpOnly; SameSite=Lax"
                    self.send_json({"ok": True}, extra_headers={"Set-Cookie": cookie})
                else:
                    self.send_json({"ok": True})
                return

            if path == "/api/auth/logout":
                self.send_json({"ok": True}, extra_headers={"Set-Cookie": "folio_session=; Path=/; Max-Age=0; HttpOnly; SameSite=Lax"})
                return

            if not self.require_auth():
                return

            if path == "/api/settings":
                if "gemini_api_key" in body:
                    set_setting("gemini_api_key", body["gemini_api_key"].strip())
                if "gemini_model" in body and body["gemini_model"].strip():
                    set_setting("gemini_model", body["gemini_model"].strip())
                if "google_client_id" in body:
                    set_setting("google_client_id", body["google_client_id"].strip())
                if "google_client_secret" in body and body["google_client_secret"].strip():
                    set_setting("google_client_secret", body["google_client_secret"].strip())
                extra_hdrs = {}
                if "folio_passcode" in body and not os.environ.get("FOLIO_PASSCODE", "").strip():
                    new_pc = body["folio_passcode"].strip()
                    set_setting("folio_passcode", new_pc)
                    if new_pc:
                        tok = make_session_token(new_pc)
                        extra_hdrs["Set-Cookie"] = f"folio_session={tok}; Path=/; Max-Age={30*86400}; HttpOnly; SameSite=Lax"
                api_key = resolve_gemini_api_key()
                self.send_json(
                    {
                        "ok": True,
                        "gemini_configured": bool(api_key),
                        "gemini_key_masked": (api_key[:4] + "..." + api_key[-4:]) if len(api_key) > 8 else ("Configured" if api_key else ""),
                        "gemini_model": get_setting("gemini_model", "gemini-4-hydrogen"),
                        "drive_oauth_connected": has_drive_oauth(),
                        "passcode_enabled": bool(get_active_passcode()),
                    },
                    extra_headers=extra_hdrs,
                )
                return

            if path == "/api/books/sync":
                books = sync_library_from_drive()
                self.send_json({"books": books})
                return

            m_prog = re.match(r"^/api/books/([^/]+)/progress$", path)
            if m_prog:
                book_id = urllib.parse.unquote(m_prog.group(1))
                ch_idx = int(body.get("chapter_index", 0))
                scroll_pct = float(body.get("scroll_percent", 0.0))
                now_iso = datetime.now(timezone.utc).isoformat()
                conn = get_db()
                try:
                    with conn:
                        conn.execute(
                            "UPDATE books SET last_chapter_index = ?, scroll_percent = ?, last_opened_at = ? WHERE id = ?",
                            (ch_idx, scroll_pct, now_iso, book_id),
                        )
                finally:
                    conn.close()
                self.send_json({"ok": True})
                return

            m_tags = re.match(r"^/api/books/([^/]+)/tags$", path)
            if m_tags:
                book_id = urllib.parse.unquote(m_tags.group(1))
                raw_tags = body.get("tags") or []
                clean_tags = []
                seen_tags = set()
                for t in raw_tags:
                    ts = str(t).strip()
                    if ts and ts.lower() not in seen_tags:
                        seen_tags.add(ts.lower())
                        clean_tags.append(ts)
                conn = get_db()
                try:
                    with conn:
                        conn.execute(
                            "UPDATE books SET tags = ? WHERE id = ?",
                            (json.dumps(clean_tags), book_id),
                        )
                finally:
                    conn.close()
                self.send_json({"ok": True, "tags": clean_tags})
                return

            m_bm = re.match(r"^/api/books/([^/]+)/bookmarks$", path)
            if m_bm:
                book_id = urllib.parse.unquote(m_bm.group(1))
                bm_id = body.get("id") or f"bm_{int(datetime.now().timestamp() * 1000)}"
                ch_idx = int(body.get("chapter_index", 0))
                ch_title = (body.get("chapter_title") or "").strip()
                scroll_pct = float(body.get("scroll_percent", 0.0))
                label = (body.get("label") or "").strip() or f"Section {ch_idx + 1} ({round(scroll_pct * 100)}%)"
                excerpt = (body.get("excerpt") or "").strip()[:240]
                now_iso = datetime.now(timezone.utc).isoformat()
                conn = get_db()
                try:
                    with conn:
                        conn.execute(
                            """
                            INSERT INTO bookmarks (id, book_id, chapter_index, chapter_title, scroll_percent, label, excerpt, created_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                label = excluded.label,
                                scroll_percent = excluded.scroll_percent
                            """,
                            (bm_id, book_id, ch_idx, ch_title, scroll_pct, label, excerpt, now_iso),
                        )
                    bm_row = conn.execute("SELECT * FROM bookmarks WHERE id = ?", (bm_id,)).fetchone()
                finally:
                    conn.close()
                self.send_json({"bookmark": dict(bm_row)})
                return

            m_gdoc = re.match(r"^/api/books/([^/]+)/export-gdoc$", path)
            if m_gdoc:
                book_id = urllib.parse.unquote(m_gdoc.group(1))
                res = export_notes_to_gdoc(book_id)
                self.send_json({"ok": True, "doc": res})
                return

            m_argmap = re.match(r"^/api/books/([^/]+)/argument-map$", path)
            if m_argmap:
                book_id = urllib.parse.unquote(m_argmap.group(1))
                ch_idx = int(body.get("chapter_index", 0))
                book = ensure_book_downloaded_and_parsed(book_id)
                chapters = (book.get("manifest") or {}).get("chapters") or []
                if ch_idx < 0 or ch_idx >= len(chapters):
                    self.send_error_json("Invalid chapter index", 400)
                    return
                ch = chapters[ch_idx]
                ch_path = BOOKS_DIR / book_id / "extracted" / ch["href"]
                ch_text = extract_plain_text_from_xhtml(ch_path.read_bytes()) if ch_path.exists() else ""
                if len(ch_text) > 50000:
                    ch_text = ch_text[:50000] + "\n...[truncated]..."

                model = get_setting("gemini_model", "gemini-4-hydrogen")
                prompt = (
                    f"Analyze the following chapter from \"{book.get('title') or book.get('name')}\" "
                    f"(Section: {ch.get('title')}) and extract a structured argument map.\n\n"
                    "Return ONLY raw JSON (no markdown code fences) with this exact schema:\n"
                    "{\n"
                    '  "thesis": "1-3 sentence synthesis of the chapter core thesis",\n'
                    '  "key_claims": [{"claim": "...", "role": "Intervention|Evidence|Synthesis", "quote": "exact 8-18 word verbatim substring from the text"}],\n'
                    '  "key_terms": [{"term": "...", "definition": "...", "quote": "exact 8-18 word verbatim substring from the text"}],\n'
                    '  "critiques": [{"target": "...", "critique": "...", "quote": "exact 8-18 word verbatim substring from the text"}]\n'
                    "}\n\n"
                    "Every `quote` MUST be copied verbatim from the chapter text below so the reader can jump directly to it.\n\n"
                    f"CHAPTER TEXT:\n{ch_text}"
                )
                res = call_gemini_interactions_api(prompt=prompt, model=model)
                raw_out = (res.get("output_text") or "").strip()
                raw_out = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_out, flags=re.I | re.S).strip()
                try:
                    parsed_map = json.loads(raw_out)
                except Exception:
                    parsed_map = {"thesis": raw_out, "key_claims": [], "key_terms": [], "critiques": []}
                self.send_json({"map": parsed_map, "model": res.get("model", model), "chapter_index": ch_idx})
                return

            if path == "/api/gemini/compare":
                book_ids = body.get("book_ids") or []
                topic = (body.get("topic") or "").strip()
                if len(book_ids) < 2 or not topic:
                    self.send_error_json("Provide at least 2 book_ids and a topic", 400)
                    return
                conn = get_db()
                try:
                    book_contexts = []
                    for bid in book_ids[:3]:
                        brow = conn.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
                        if not brow:
                            continue
                        bdict = dict(brow)
                        anns = conn.execute(
                            "SELECT chapter_title, page_number, color, selected_text, note FROM annotations WHERE book_id = ? LIMIT 15",
                            (bid,),
                        ).fetchall()
                        ann_lines = [
                            f"- [{a['color']}] ({a['chapter_title']} {a['page_number'] or ''}): \"{a['selected_text'][:220]}\""
                            + (f" — Note: {a['note']}" if a["note"] else "")
                            for a in anns
                        ]
                        passages = []
                        if bdict.get("downloaded"):
                            hits = search_book_passages(bid, topic, max_results=4)
                            passages = [f"- ({h['chapter_title']}): {h['snippet'][:350]}" for h in hits]
                        ctx = (
                            f"BOOK: {bdict.get('title') or bdict.get('name')} by {bdict.get('author') or 'Unknown'}\n"
                            f"Highlights:\n" + ("\n".join(ann_lines) if ann_lines else "(none)") + "\n"
                            f"Relevant Passages:\n" + ("\n".join(passages) if passages else "(book not cached locally yet)")
                        )
                        book_contexts.append(ctx)
                finally:
                    conn.close()

                model = get_setting("gemini_model", "gemini-4-hydrogen")
                prompt = (
                    f"Compare how the following books approach the concept/question: \"{topic}\".\n"
                    "Structure your response in clear Markdown with:\n"
                    "1. Core Theses & Framings\n2. Key Points of Agreement & Divergence\n3. Cited Passages & Concepts from Each Work\n\n"
                    + "\n\n---\n\n".join(book_contexts)
                )
                res = call_gemini_interactions_api(prompt=prompt, model=model)
                self.send_json({"comparison": res.get("output_text", ""), "model": res.get("model", model)})
                return

            m_ann = re.match(r"^/api/books/([^/]+)/annotations$", path)
            if m_ann:
                book_id = urllib.parse.unquote(m_ann.group(1))
                ann_id = body.get("id") or f"ann_{int(datetime.now().timestamp() * 1000)}"
                now_iso = datetime.now(timezone.utc).isoformat()
                conn = get_db()
                try:
                    with conn:
                        conn.execute(
                            """
                            INSERT INTO annotations
                            (id, book_id, chapter_index, chapter_href, chapter_title, page_number, selected_text, note, gemini_response, color, occurrence_index, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                page_number = COALESCE(NULLIF(excluded.page_number, ''), annotations.page_number),
                                note = excluded.note,
                                gemini_response = excluded.gemini_response,
                                color = excluded.color,
                                updated_at = excluded.updated_at
                            """,
                            (
                                ann_id,
                                book_id,
                                int(body.get("chapter_index", 0)),
                                body.get("chapter_href", ""),
                                body.get("chapter_title", ""),
                                body.get("page_number", ""),
                                body.get("selected_text", ""),
                                body.get("note", ""),
                                body.get("gemini_response", ""),
                                body.get("color", "yellow"),
                                int(body.get("occurrence_index", 0)),
                                now_iso,
                                now_iso,
                            ),
                        )
                    row = conn.execute("SELECT * FROM annotations WHERE id = ?", (ann_id,)).fetchone()
                finally:
                    conn.close()
                schedule_book_auto_sync(book_id)
                self.send_json({"annotation": dict(row)})
                return

            m_sync_drive = re.match(r"^/api/books/([^/]+)/sync-drive$", path)
            if m_sync_drive:
                book_id = urllib.parse.unquote(m_sync_drive.group(1))
                res = sync_annotations_to_drive(book_id)
                self.send_json(res)
                return

            m_report = re.match(r"^/api/books/([^/]+)/report$", path)
            if m_report:
                book_id = urllib.parse.unquote(m_report.group(1))
                mode = body.get("mode") or "structured"  # "structured" or "gemini"
                book = ensure_book_downloaded_and_parsed(book_id)
                conn = get_db()
                try:
                    anns = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT * FROM annotations WHERE book_id = ? ORDER BY chapter_index ASC, created_at ASC",
                            (book_id,),
                        ).fetchall()
                    ]
                finally:
                    conn.close()

                if not anns:
                    self.send_error_json("No highlights found for this book yet. Highlight some passages first!", 400)
                    return

                # Build structured category breakdown
                grouped_lines = [
                    f"# Reading Report: {book.get('title') or book.get('name')}",
                    f"**Author:** {book.get('author') or 'Unknown'}  ",
                    f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · **Total Highlights:** {len(anns)}",
                    "",
                ]

                for c_key, meta in HIGHLIGHT_SCHEMA.items():
                    items = [a for a in anns if (a.get("color") or "yellow").lower() == c_key]
                    grouped_lines.append(f"## {meta['label']} ({c_key.capitalize()} — {len(items)})")
                    grouped_lines.append(f"*{meta['description']}*")
                    grouped_lines.append("")
                    if not items:
                        grouped_lines.append("_No passages highlighted in this category yet._")
                        grouped_lines.append("")
                    else:
                        for it in items:
                            ch_label = it.get("chapter_title") or f"Section {it.get('chapter_index', 0) + 1}"
                            pg_label = f", {it['page_number']}" if it.get("page_number") else ""
                            grouped_lines.append(f"- **[{ch_label}{pg_label}]** \"{it.get('selected_text', '').strip()}\"")
                            if it.get("note"):
                                grouped_lines.append(f"  - **Reader Note:** {it['note'].strip()}")
                            if it.get("gemini_response"):
                                grouped_lines.append(f"  - **Gemini Note:** {it['gemini_response'].strip()}")
                        grouped_lines.append("")

                structured_md = "\n".join(grouped_lines)

                if mode == "gemini":
                    model = body.get("model") or get_setting("gemini_model", "gemini-4-hydrogen")
                    sys_inst = (
                        f"You are an expert academic research assistant generating a formal Reading Report for \"{book.get('title')}\" by {book.get('author')}.\n"
                        "The reader uses a strict 5-color analytical coding system:\n"
                        "- BLUE (Critical Points): Key interventions or important components of the overall argument.\n"
                        "- YELLOW (Significant Points): Main ideas of paragraphs and supporting claims.\n"
                        "- RED (Critiques): The author's critiques of other scholarship and interlocutors.\n"
                        "- GREEN (Definitions): Key terms or operationalization of key variables.\n"
                        "- GREY (Concerns): Passages the reader disagrees with or finds confusing/ambiguous.\n\n"
                        "Synthesize the reader's color-coded highlights and notes into an executive scholarly Reading Report with dedicated sections:\n"
                        "1. **Core Thesis & Critical Interventions (from Blue & Yellow highlights)**\n"
                        "2. **Lexicon: Definitions & Operationalization of Key Variables (from Green highlights)**\n"
                        "3. **Historiographical & Theoretical Critiques: What the Author Says About Other Scholars (from Red highlights)**\n"
                        "4. **Critical Tensions, Concerns & Clarifications (addressing Grey highlights — unpack any confusing passages and evaluate the reader's disagreements)**\n"
                        "Quote directly from the reader's highlights and cite the chapter/section titles."
                    )
                    try:
                        gem_res = call_gemini_interactions_api(
                            prompt=f"Here are all of the reader's color-coded highlights and notes for the book:\n\n{structured_md}\n\nPlease generate the comprehensive scholarly Reading Report.",
                            system_instruction=sys_inst,
                            model=model,
                        )
                        final_report = gem_res["output_text"] + "\n\n---\n\n" + structured_md
                    except RuntimeError as err:
                        if "MISSING_API_KEY" in str(err):
                            self.send_error_json(
                                "Gemini API key is required for AI synthesis. Use 'Instant Report' or set your Gemini API key in Settings.",
                                status=401,
                                code="MISSING_API_KEY",
                            )
                            return
                        raise
                else:
                    final_report = structured_md

                set_setting(f"report_{book_id}", final_report)
                self.send_json({"report_markdown": final_report, "mode": mode})
                return

            m_gemini = re.match(r"^/api/books/([^/]+)/gemini$", path)
            if m_gemini:
                book_id = urllib.parse.unquote(m_gemini.group(1))
                question = (body.get("question") or "").strip()
                selected_text = (body.get("selected_text") or "").strip()
                action = body.get("action") or "ask"  # explain, summarize, critique, ask
                chapter_index = int(body.get("chapter_index", 0))
                scope = body.get("scope") or "chapter"  # selection, chapter, book
                prev_id = (body.get("previous_interaction_id") or "").strip()
                model = body.get("model") or get_setting("gemini_model", "gemini-4-hydrogen")

                book = ensure_book_downloaded_and_parsed(book_id)
                ch_title, ch_text = get_book_chapter_text(book_id, chapter_index)

                # Gather user's highlights for this book to enrich context
                conn = get_db()
                try:
                    anns = [
                        dict(r)
                        for r in conn.execute(
                            "SELECT chapter_title, selected_text, note FROM annotations WHERE book_id = ? ORDER BY chapter_index ASC, created_at ASC LIMIT 30",
                            (book_id,),
                        ).fetchall()
                    ]
                finally:
                    conn.close()

                sys_parts = [
                    f"You are an insightful, rigorous academic and literary reading companion helping a reader study the book \"{book.get('title')}\" by {book.get('author')}.",
                    f"Current Chapter ({chapter_index + 1}): \"{ch_title}\".",
                    "Quote or reference specific concepts from the provided text accurately, and keep explanations clear, well-structured, and directly helpful.",
                ]

                context_blocks = []
                if selected_text:
                    context_blocks.append(f"=== SELECTED PASSAGE ===\n\"{selected_text}\"")

                if scope in ("chapter", "book") and ch_text:
                    # Include up to ~24,000 chars of current chapter text
                    trimmed_ch = ch_text[:24000]
                    context_blocks.append(f"=== CURRENT CHAPTER TEXT ({ch_title}) ===\n{trimmed_ch}")

                if scope == "book" and (question or selected_text):
                    search_q = f"{question} {selected_text[:200]}".strip()
                    matches = search_book_passages(book_id, search_q, max_results=6)
                    if matches:
                        passages_str = "\n\n".join(
                            f"[{m['chapter_title']}]: {m['snippet']}" for m in matches
                        )
                        context_blocks.append(f"=== RELEVANT PASSAGES ACROSS THE BOOK ===\n{passages_str}")

                if anns:
                    ann_lines = []
                    for a in anns[:25]:
                        c_key = (a.get("color") or "yellow").lower()
                        c_meta = HIGHLIGHT_SCHEMA.get(c_key, {"short": c_key.upper()})
                        line = f"- [{c_key.upper()} / {c_meta['short']}] ({a['chapter_title']}): \"{a['selected_text'][:260]}\""
                        if a.get("note"):
                            line += f" | Reader Note: {a['note']}"
                        ann_lines.append(line)
                    context_blocks.append(
                        "=== READER'S COLOR-CODED HIGHLIGHTS & NOTES ===\n"
                        "(System: Blue=Critical Points/Interventions, Yellow=Significant Main Ideas, Red=Author's Critiques of Other Scholarship, Green=Definitions/Operationalization, Grey=Reader's Concerns/Disagreements/Confusion)\n"
                        + "\n".join(ann_lines)
                    )

                if action == "explain" and not question:
                    user_task = "Explain the selected passage in clear detail. Unpack its key terms, theoretical or narrative context within this chapter, and why it matters to the author's broader argument."
                elif action == "summarize" and not question:
                    if selected_text:
                        user_task = "Summarize the core argument and key takeaways of this selected passage concisely."
                    else:
                        user_task = f"Provide a structured summary of the key arguments, themes, and structure of this chapter ({ch_title})."
                elif action == "critique" and not question:
                    user_task = "Analyze the assumptions, theoretical framing, and potential tensions or counterarguments in this passage."
                else:
                    user_task = question or "Explain this passage."

                full_prompt = "\n\n".join(context_blocks + [f"=== READER REQUEST ===\n{user_task}"])

                try:
                    gem_res = call_gemini_interactions_api(
                        prompt=full_prompt,
                        system_instruction="\n".join(sys_parts),
                        model=model,
                        previous_interaction_id=prev_id,
                    )
                except RuntimeError as err:
                    if "MISSING_API_KEY" in str(err):
                        self.send_error_json(
                            "Gemini API key is required. Click the Settings (Key) button in the top bar to enter your GEMINI_API_KEY.",
                            status=401,
                            code="MISSING_API_KEY",
                        )
                        return
                    raise

                now_iso = datetime.now(timezone.utc).isoformat()
                user_msg_id = f"chat_{int(datetime.now().timestamp() * 1000)}_u"
                asst_msg_id = f"chat_{int(datetime.now().timestamp() * 1000)}_a"

                conn = get_db()
                try:
                    with conn:
                        conn.execute(
                            """
                            INSERT INTO chats (id, book_id, chapter_index, role, content, selected_text, interaction_id, model, created_at)
                            VALUES (?, ?, ?, 'user', ?, ?, ?, ?, ?)
                            """,
                            (user_msg_id, book_id, chapter_index, user_task, selected_text, prev_id, model, now_iso),
                        )
                        conn.execute(
                            """
                            INSERT INTO chats (id, book_id, chapter_index, role, content, selected_text, interaction_id, model, created_at)
                            VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?, ?)
                            """,
                            (
                                asst_msg_id,
                                book_id,
                                chapter_index,
                                gem_res["output_text"],
                                selected_text,
                                gem_res["id"],
                                model,
                                now_iso,
                            ),
                        )
                finally:
                    conn.close()

                self.send_json(
                    {
                        "interaction_id": gem_res["id"],
                        "answer": gem_res["output_text"],
                        "model": model,
                        "user_message": {
                            "id": user_msg_id,
                            "role": "user",
                            "content": user_task,
                            "selected_text": selected_text,
                            "chapter_index": chapter_index,
                            "created_at": now_iso,
                        },
                        "assistant_message": {
                            "id": asst_msg_id,
                            "role": "assistant",
                            "content": gem_res["output_text"],
                            "selected_text": selected_text,
                            "interaction_id": gem_res["id"],
                            "model": model,
                            "chapter_index": chapter_index,
                            "created_at": now_iso,
                        },
                    }
                )
                return

            self.send_error_json("Unknown endpoint", 404)

        except Exception as e:
            traceback.print_exc()
            self.send_error_json(str(e), status=500)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        try:
            if not self.require_auth():
                return
            m_del_ann = re.match(r"^/api/books/([^/]+)/annotations/([^/]+)$", path)
            if m_del_ann:
                book_id = urllib.parse.unquote(m_del_ann.group(1))
                ann_id = urllib.parse.unquote(m_del_ann.group(2))
                conn = get_db()
                try:
                    with conn:
                        conn.execute("DELETE FROM annotations WHERE id = ? AND book_id = ?", (ann_id, book_id))
                finally:
                    conn.close()
                schedule_book_auto_sync(book_id)
                self.send_json({"ok": True})
                return

            m_del_chats = re.match(r"^/api/books/([^/]+)/chats$", path)
            if m_del_chats:
                book_id = urllib.parse.unquote(m_del_chats.group(1))
                conn = get_db()
                try:
                    with conn:
                        conn.execute("DELETE FROM chats WHERE book_id = ?", (book_id,))
                finally:
                    conn.close()
                self.send_json({"ok": True})
                return

            m_del_bm = re.match(r"^/api/books/([^/]+)/bookmarks/([^/]+)$", path)
            if m_del_bm:
                book_id = urllib.parse.unquote(m_del_bm.group(1))
                bm_id = urllib.parse.unquote(m_del_bm.group(2))
                conn = get_db()
                try:
                    with conn:
                        conn.execute("DELETE FROM bookmarks WHERE id = ? AND book_id = ?", (bm_id, book_id))
                finally:
                    conn.close()
                self.send_json({"ok": True})
                return

            self.send_error_json("Unknown DELETE endpoint", 404)
        except Exception as e:
            traceback.print_exc()
            self.send_error_json(str(e), status=500)


def main():
    init_db()
    start_periodic_drive_sync()
    port = int(os.environ.get("PORT", 8765))
    host = os.environ.get("HOST", "0.0.0.0" if (os.environ.get("PORT") or os.environ.get("RAILWAY_ENVIRONMENT")) else "127.0.0.1")
    server = ThreadingHTTPServer((host, port), ReaderRequestHandler)
    print(f"📚 Folio & Gemini EPUB Reader running at http://{host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.server_close()


if __name__ == "__main__":
    main()
