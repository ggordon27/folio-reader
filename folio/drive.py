"""Google Drive OAuth2 REST API + gdrive CLI sync, library catalog, and book caching."""

from __future__ import annotations

import concurrent.futures
import io
import json
import os
import posixpath
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import httpx

from .config import (
    BASE_DIR,
    BOOKS_DIR,
    COVERS_DIR,
    DATA_DIR,
    GDRIVE_BIN,
    IGNORED_FOLDER_IDS,
    IGNORED_FOLDER_NAMES,
)
from .db import (
    compute_progress_summary,
    get_db,
    get_setting,
    index_book_chapters_fts,
    set_setting,
    upsert_annotation_fts,
)
from .epub import invalidate_epub_cache, parse_epub_from_zip
from .export import companion_filename_for, format_annotations_markdown

_DRIVE_TOKEN_CACHE = {"access_token": "", "expires_at": 0.0}
_DRIVE_TOKEN_LOCK = threading.Lock()

_META_ENRICH_LOCK = threading.Lock()
_META_ENRICH_RUNNING = False

_BOOK_LOCKS_GUARD = threading.Lock()
_BOOK_DOWNLOAD_LOCKS: Dict[str, threading.Lock] = {}

_AUTO_SYNC_TIMERS: Dict[str, threading.Timer] = {}
_AUTO_SYNC_LOCK = threading.Lock()


def _get_book_lock(book_id: str) -> threading.Lock:
    with _BOOK_LOCKS_GUARD:
        lock = _BOOK_DOWNLOAD_LOCKS.get(book_id)
        if lock is None:
            lock = threading.Lock()
            _BOOK_DOWNLOAD_LOCKS[book_id] = lock
        return lock


def run_gdrive(args: list, timeout: int = 60) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["PATH"] = f"/Users/dr_geoff/.gemini/antigravity/bin:{env.get('PATH', '')}"
    cmd = [GDRIVE_BIN] + args
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout)


def get_drive_oauth_config() -> dict:
    return {
        "client_id": os.environ.get("GOOGLE_CLIENT_ID", "").strip() or get_setting("google_client_id", "").strip(),
        "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", "").strip() or get_setting("google_client_secret", "").strip(),
        "refresh_token": os.environ.get("GOOGLE_REFRESH_TOKEN", "").strip() or get_setting("google_refresh_token", "").strip(),
    }


def has_drive_oauth() -> bool:
    cfg = get_drive_oauth_config()
    return bool(cfg["client_id"] and cfg["client_secret"] and cfg["refresh_token"])


def set_drive_token_cache(access_token: str, expires_in: float = 3500.0):
    with _DRIVE_TOKEN_LOCK:
        _DRIVE_TOKEN_CACHE["access_token"] = access_token
        _DRIVE_TOKEN_CACHE["expires_at"] = time.time() + float(expires_in)


def get_drive_access_token() -> str:
    with _DRIVE_TOKEN_LOCK:
        if _DRIVE_TOKEN_CACHE["access_token"] and time.time() < _DRIVE_TOKEN_CACHE["expires_at"] - 60:
            return _DRIVE_TOKEN_CACHE["access_token"]
        cfg = get_drive_oauth_config()
        if not (cfg["client_id"] and cfg["client_secret"] and cfg["refresh_token"]):
            raise RuntimeError("Google Drive OAuth is not configured.")
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": cfg["client_id"],
                    "client_secret": cfg["client_secret"],
                    "refresh_token": cfg["refresh_token"],
                    "grant_type": "refresh_token",
                },
            )
            if resp.status_code != 200:
                raise RuntimeError(f"Google OAuth token refresh failed ({resp.status_code}): {resp.text}")
            data = resp.json()
        tok = data["access_token"]
        _DRIVE_TOKEN_CACHE["access_token"] = tok
        _DRIVE_TOKEN_CACHE["expires_at"] = time.time() + float(data.get("expires_in", 3500))
        return tok


def drive_rest_json(url: str, method: str = "GET", body_bytes: Optional[bytes] = None, content_type: str = "application/json") -> dict:
    token = get_drive_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    if body_bytes is not None:
        headers["Content-Type"] = content_type
    with httpx.Client(timeout=60.0) as client:
        resp = client.request(method, url, content=body_bytes, headers=headers)
        if resp.status_code >= 400:
            raise RuntimeError(f"Google Drive API error ({resp.status_code}): {resp.text}")
        return resp.json() if resp.text else {}


def drive_rest_download(file_id: str, out_path: Path):
    token = get_drive_access_token()
    url = f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id)}?alt=media"
    with httpx.Client(timeout=120.0, follow_redirects=True) as client:
        with client.stream("GET", url, headers={"Authorization": f"Bearer {token}"}) as resp:
            if resp.status_code >= 400:
                raise RuntimeError(f"Google Drive download error ({resp.status_code}): {resp.read().decode('utf-8', errors='replace')}")
            with out_path.open("wb") as f:
                for chunk in resp.iter_bytes(chunk_size=65536):
                    f.write(chunk)


def drive_rest_upload_file(
    name: str,
    data_bytes: bytes,
    mime_type: str,
    parent_id: str = "",
    existing_id: str = "",
    target_mime: str = "",
) -> dict:
    token = get_drive_access_token()
    boundary = f"folio_{secrets.token_hex(12)}"
    meta: dict = {"name": name}
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
    with httpx.Client(timeout=60.0) as client:
        resp = client.request(
            method,
            url,
            content=body,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Google Drive upload error ({resp.status_code}): {resp.text}")
        return resp.json()


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


def is_ignored_epub(item_or_book: dict, extra_ignored_folder_ids: Optional[Set[str]] = None) -> bool:
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
        item_or_book.get("id") == "1Xx-8moh8UVB9Y_BVTNBkR2uLIbhIjvG0"
        or name == "book.epub"
        or "getting started with adobe digital editions" in name
        or "getting started with adobe digital editions" in title
        or "process-tracing methods" in name
        or "process-tracing methods" in title
        or "elevenreader" in name
        or "(tts)" in name
        or "-tts.epub" in name
        or "_tts.epub" in name
    ):
        return True
    return False


def deduplicate_books(books: list) -> list:
    """Keeps one canonical entry per book, preferring annotated/cached/foldered copies."""
    def _split_title(s: str) -> Tuple[str, str, str]:
        s = re.sub(r"\.epub$", "", (s or "").lower())
        s = re.sub(r"_for_geoffrey[-_]gordon", "", s)
        parts = re.split(r"[:;—]", s, 1)
        main = re.sub(r"[^a-z0-9]+", " ", parts[0]).strip()
        sub = re.sub(r"[^a-z0-9]+", " ", parts[1]).strip() if len(parts) > 1 else ""
        full = (main + " " + sub).strip()
        return full, main, sub

    def _author_tokens(s: str) -> Set[str]:
        toks = set(re.findall(r"[a-z]{3,}", (s or "").lower()))
        return toks - {"various", "unknown", "editor", "edited", "and"}

    def _score(b: dict) -> tuple:
        return (
            1 if (b.get("annotation_count") or 0) > 0 else 0,
            1 if b.get("downloaded") else 0,
            1 if b.get("last_opened_at") else 0,
            1 if (b.get("folder_path") or "").strip() else 0,
            1 if (b.get("meta_verified") or 0) else 0,
            1 if (b.get("author") or "").strip() else 0,
            b.get("size") or 0,
            b.get("modified_time") or "",
        )

    groups: Dict[str, list] = {}
    for b in books:
        full, main, sub = _split_title(b.get("title") or b.get("name") or "")
        key = main if (len(main) >= 10 and len(main.split()) >= 2) else (full or b["id"])
        groups.setdefault(key, []).append((b, full, main, sub))

    keep_ids = set()
    for items in groups.values():
        if len(items) == 1:
            keep_ids.add(items[0][0]["id"])
            continue
        reps: List[tuple] = []
        for it, full, main, sub in sorted(items, key=lambda x: _score(x[0]), reverse=True):
            if (it.get("annotation_count") or 0) > 0:
                keep_ids.add(it["id"])
                reps.append((it, full, main, sub))
                continue
            matched = False
            at = _author_tokens(it.get("author") or "")
            for rit, rfull, rmain, rsub in reps:
                if full == rfull:
                    matched = True
                    break
                if not sub or not rsub or sub.startswith(rsub[:12]) or rsub.startswith(sub[:12]):
                    matched = True
                    break
                if at & _author_tokens(rit.get("author") or ""):
                    matched = True
                    break
            if not matched:
                keep_ids.add(it["id"])
                reps.append((it, full, main, sub))

    return [b for b in books if b["id"] in keep_ids]


def get_all_books() -> list:
    """
    Returns slim library book rows (omitting manifest_json) using a single LEFT JOIN
    for annotation counts instead of an N+1 correlated subquery.
    """
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT b.id, b.name, b.title, b.author, b.publisher, b.cover_href, b.opf_dir,
                   b.parent_folder_id, b.folder_path, b.tags, b.meta_verified,
                   b.size, b.modified_time, b.web_view_link, b.downloaded,
                   b.last_chapter_index, b.scroll_percent, b.last_opened_at,
                   b.annotations_drive_id, b.last_synced_at,
                   b.chapter_count, b.total_words, b.last_chapter_title, b.progress_percent,
                   COALESCE(b.has_cover, 0) AS has_cover,
                   COALESCE(ac.cnt, 0) AS annotation_count
            FROM books b
            LEFT JOIN (
                SELECT book_id, COUNT(*) AS cnt FROM annotations GROUP BY book_id
            ) ac ON ac.book_id = b.id
            WHERE b.id NOT IN (SELECT id FROM hidden_books)
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
            raw_tags = d.get("tags") or "[]"
            try:
                d["tags"] = json.loads(raw_tags) if isinstance(raw_tags, str) else list(raw_tags)
            except Exception:
                d["tags"] = []
            result.append(d)
        return deduplicate_books(result)
    finally:
        conn.close()


def hide_book(book_id: str) -> dict:
    """
    Removes a book from the library and records its Drive ID in hidden_books
    so future Google Drive syncs never re-add it. Cleans up local cached EPUB/cover
    while preserving any annotations in case the user restores it later.
    """
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        book = dict(row) if row else {"id": book_id, "name": "", "title": "", "author": ""}
        # Store lightweight snapshot (without manifest_json) for instant Undo/Restore
        snap = {k: v for k, v in book.items() if k != "manifest_json"}
        with conn:
            conn.execute(
                """
                INSERT INTO hidden_books (id, name, title, author, book_json, hidden_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    title = excluded.title,
                    author = excluded.author,
                    book_json = excluded.book_json,
                    hidden_at = excluded.hidden_at
                """,
                (
                    book_id,
                    book.get("name") or "",
                    book.get("title") or book.get("name") or "",
                    book.get("author") or "",
                    json.dumps(snap, ensure_ascii=False),
                    now_iso,
                ),
            )
            conn.execute("DELETE FROM books WHERE id = ?", (book_id,))
            conn.execute("DELETE FROM chapter_text_fts WHERE book_id = ?", (book_id,))
    finally:
        conn.close()

    # Clean up local cached EPUB and cover thumbnail
    bdir = BOOKS_DIR / book_id
    if bdir.exists():
        shutil.rmtree(bdir, ignore_errors=True)
    for cov in COVERS_DIR.glob(f"{book_id}.*"):
        try:
            cov.unlink()
        except OSError:
            pass
    invalidate_epub_cache(book_id)
    return {"ok": True, "book_id": book_id, "title": book.get("title") or book.get("name") or book_id}


def list_hidden_books() -> list:
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, title, author, hidden_at FROM hidden_books ORDER BY hidden_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def restore_hidden_book(book_id: str) -> dict:
    """Restores a previously hidden book back into the library immediately."""
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM hidden_books WHERE id = ?", (book_id,)).fetchone()
        restored_book = None
        with conn:
            conn.execute("DELETE FROM hidden_books WHERE id = ?", (book_id,))
            if row:
                snap = {}
                if row["book_json"]:
                    try:
                        snap = json.loads(row["book_json"])
                    except Exception:
                        snap = {}
                name = snap.get("name") or row["name"] or f"{book_id}.epub"
                title = snap.get("title") or row["title"] or name
                author = snap.get("author") or row["author"] or ""
                conn.execute(
                    """
                    INSERT OR IGNORE INTO books
                    (id, name, title, author, publisher, cover_href, opf_dir, parent_folder_id,
                     folder_path, tags, meta_verified, size, modified_time, web_view_link,
                     downloaded, last_chapter_index, scroll_percent, last_opened_at,
                     annotations_drive_id, last_synced_at, chapter_count, total_words,
                     last_chapter_title, progress_percent, has_cover)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        book_id,
                        name,
                        title,
                        author,
                        snap.get("publisher") or "",
                        snap.get("cover_href") or "",
                        snap.get("opf_dir") or "",
                        snap.get("parent_folder_id") or "",
                        snap.get("folder_path") or "",
                        snap.get("tags") if isinstance(snap.get("tags"), str) else json.dumps(snap.get("tags") or []),
                        int(snap.get("meta_verified") or 0),
                        int(snap.get("size") or 0),
                        snap.get("modified_time") or "",
                        snap.get("web_view_link") or "",
                        int(snap.get("last_chapter_index") or 0),
                        float(snap.get("scroll_percent") or 0.0),
                        snap.get("last_opened_at"),
                        snap.get("annotations_drive_id") or "",
                        snap.get("last_synced_at") or "",
                        int(snap.get("chapter_count") or 0),
                        int(snap.get("total_words") or 0),
                        snap.get("last_chapter_title") or "",
                        int(snap.get("progress_percent") or 0),
                    ),
                )
        brow = conn.execute(
            """
            SELECT b.id, b.name, b.title, b.author, b.publisher, b.cover_href, b.opf_dir,
                   b.parent_folder_id, b.folder_path, b.tags, b.meta_verified,
                   b.size, b.modified_time, b.web_view_link, b.downloaded,
                   b.last_chapter_index, b.scroll_percent, b.last_opened_at,
                   b.annotations_drive_id, b.last_synced_at,
                   b.chapter_count, b.total_words, b.last_chapter_title, b.progress_percent,
                   COALESCE(b.has_cover, 0) AS has_cover,
                   (SELECT COUNT(*) FROM annotations a WHERE a.book_id = b.id) AS annotation_count
            FROM books b WHERE b.id = ?
            """,
            (book_id,),
        ).fetchone()
        if brow:
            restored_book = dict(brow)
            try:
                restored_book["tags"] = json.loads(restored_book.get("tags") or "[]")
            except Exception:
                restored_book["tags"] = []
        return {"ok": True, "book": restored_book}
    finally:
        conn.close()


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def get_epub_cache_stats(days: int = 10) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_db()
    try:
        total_cached = conn.execute("SELECT COUNT(*) FROM books WHERE downloaded = 1").fetchone()[0]
        stale_count = conn.execute(
            "SELECT COUNT(*) FROM books WHERE downloaded = 1 AND (last_opened_at IS NULL OR last_opened_at < ?)",
            (cutoff,),
        ).fetchone()[0]
    finally:
        conn.close()
    return {
        "total_cached": total_cached,
        "stale_count": stale_count,
        "cache_bytes": _dir_size_bytes(BOOKS_DIR),
        "days": days,
    }


def prune_stale_epub_cache(days: int = 10) -> dict:
    """Removes cached EPUB files unopened for >= `days` days, preserving all highlights, notes, tags & progress."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id FROM books WHERE downloaded = 1 AND (last_opened_at IS NULL OR last_opened_at < ?)",
            (cutoff,),
        ).fetchall()
        stale_ids = [r["id"] for r in rows]
        freed_bytes = 0
        for bid in stale_ids:
            bdir = BOOKS_DIR / bid
            freed_bytes += _dir_size_bytes(bdir)
            shutil.rmtree(bdir, ignore_errors=True)
            invalidate_epub_cache(bid)
        if stale_ids:
            with conn:
                placeholders = ",".join("?" for _ in stale_ids)
                conn.execute(f"UPDATE books SET downloaded = 0 WHERE id IN ({placeholders})", tuple(stale_ids))
    finally:
        conn.close()
    return {"cleared_count": len(stale_ids), "freed_bytes": freed_bytes, "days": days}


def ensure_book_downloaded_and_parsed(book_id: str) -> dict:
    """
    Ensures book.epub is downloaded locally and parsed directly from the ZIP archive
    (no extracted/ directory). Populates chapter_text_fts and precomputed progress columns.
    """
    with _get_book_lock(book_id):
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
        legacy_extract_dir = book_folder / "extracted"

        if not epub_file.exists() or not book.get("manifest_json"):
            book_folder.mkdir(parents=True, exist_ok=True)
            if not epub_file.exists():
                if has_drive_oauth():
                    drive_rest_download(book_id, epub_file)
                else:
                    proc = run_gdrive(["readonly", "download", book_id, "--out", str(epub_file)], timeout=120)
                    if proc.returncode != 0:
                        raise RuntimeError(f"Failed to download EPUB from Google Drive: {proc.stderr.strip()}")

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

            manifest, chapter_plain_texts = parse_epub_from_zip(epub_file)
            invalidate_epub_cache(book_id)
            if legacy_extract_dir.exists():
                shutil.rmtree(legacy_extract_dir, ignore_errors=True)

            now_iso = datetime.now(timezone.utc).isoformat()
            ch_cnt, tot_words, ch_title, pct = compute_progress_summary(
                manifest, book.get("last_chapter_index") or 0, book.get("scroll_percent") or 0.0
            )

            conn = get_db()
            try:
                with conn:
                    conn.execute(
                        """
                        UPDATE books
                        SET title = ?, author = ?, publisher = ?, cover_href = ?, opf_dir = ?,
                            parent_folder_id = COALESCE(NULLIF(?, ''), parent_folder_id),
                            downloaded = 1, meta_verified = 1, last_opened_at = ?, manifest_json = ?,
                            chapter_count = ?, total_words = ?, last_chapter_title = ?, progress_percent = ?
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
                            ch_cnt,
                            tot_words,
                            ch_title,
                            pct,
                            book_id,
                        ),
                    )
                    index_book_chapters_fts(book_id, chapter_plain_texts, conn=conn)
            finally:
                conn.close()

            try_import_annotations_from_drive(book_id, book["name"])
        else:
            # Clean up any leftover legacy extracted/ folder if book.epub is present
            if legacy_extract_dir.exists():
                shutil.rmtree(legacy_extract_dir, ignore_errors=True)
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


def sync_library_from_drive() -> list:
    """Syncs EPUB catalog from Google Drive (OAuth REST or local gdrive CLI) into SQLite."""
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

    # Merge permanently user-removed books from SQLite hidden_books so Drive sync never re-adds them
    conn_h = get_db()
    try:
        for r in conn_h.execute("SELECT id FROM hidden_books").fetchall():
            ignored_file_ids.add(r["id"])
    finally:
        conn_h.close()

    dynamic_ignored_folders = set(IGNORED_FOLDER_IDS)
    if has_drive_oauth():
        try:
            q_folders = (
                "trashed = false and mimeType = 'application/vnd.google-apps.folder' and "
                "(name = 'ElevenReader epubs' or name = 'epub_out' or name = 'output_epubs' "
                "or name = 'tts-epub converter' or name = 'tts-epub converter2' or name = '_Duplicates_To_Trash')"
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

                existing = conn.execute("SELECT id FROM books WHERE id = ?", (file_id,)).fetchone()
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
        start_background_metadata_enrichment(limit=60)
        return get_all_books()
    finally:
        conn.close()


def save_cover_thumbnail(book_id: str, cover_bytes: bytes, cover_href: str) -> bool:
    """Saves cover image bytes to DATA_DIR/covers/<book_id>.<ext> (capped at 1.5 MB)."""
    if not cover_bytes or len(cover_bytes) > 1_500_000:
        return False
    ext = Path(cover_href).suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp", ".gif"):
        ext = ".jpg"
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    # Remove any previous cover with a different extension
    for old in COVERS_DIR.glob(f"{book_id}.*"):
        try:
            old.unlink()
        except OSError:
            pass
    (COVERS_DIR / f"{book_id}{ext}").write_bytes(cover_bytes)
    return True


def find_cached_cover(book_id: str) -> Optional[Path]:
    if not COVERS_DIR.is_dir():
        return None
    for p in COVERS_DIR.glob(f"{book_id}.*"):
        if p.is_file():
            return p
    return None


class _RangeHTTPFile(io.RawIOBase):
    """Seekable read-only file backed by HTTP Range requests so zipfile reads only the CD + OPF."""

    def __init__(self, url: str, headers: dict, size: int, client: httpx.Client):
        self._url = url
        self._headers = headers
        self._size = size
        self._client = client
        self._pos = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = max(0, offset)
        elif whence == io.SEEK_CUR:
            self._pos = max(0, self._pos + offset)
        elif whence == io.SEEK_END:
            self._pos = max(0, self._size + offset)
        return self._pos

    def tell(self) -> int:
        return self._pos

    def read(self, size: int = -1) -> bytes:
        if self._pos >= self._size:
            return b""
        if size is None or size < 0:
            end = self._size - 1
        else:
            if size == 0:
                return b""
            end = min(self._size - 1, self._pos + size - 1)
        hdrs = dict(self._headers)
        hdrs["Range"] = f"bytes={self._pos}-{end}"
        resp = self._client.get(self._url, headers=hdrs)
        if resp.status_code not in (200, 206):
            raise RuntimeError(f"Range fetch failed ({resp.status_code})")
        data = resp.content
        self._pos += len(data)
        return data


def _extract_opf_and_cover_from_zip(zf: zipfile.ZipFile) -> Tuple[str, str, str, Optional[bytes], str]:
    """Extracts (title, author, publisher, cover_bytes, cover_href) from an open ZipFile."""
    def _strip(tag: str) -> str:
        return tag.split("}", 1)[1] if "}" in tag else tag

    names = zf.namelist()
    opf_path = None
    if "META-INF/container.xml" in names:
        try:
            tree = ET.fromstring(zf.read("META-INF/container.xml"))
            for el in tree.iter():
                if _strip(el.tag) == "rootfile" and el.attrib.get("full-path"):
                    opf_path = el.attrib["full-path"]
                    break
        except Exception:
            pass
    if not opf_path:
        opfs = [n for n in names if n.lower().endswith(".opf")]
        opf_path = opfs[0] if opfs else None
    if not opf_path or opf_path not in names:
        return "", "", "", None, ""

    root = ET.fromstring(zf.read(opf_path))
    opf_dir = posixpath.dirname(opf_path.replace("\\", "/"))

    titles: List[str] = []
    creators: List[str] = []
    contributors: List[str] = []
    publisher = ""
    refinements: Dict[str, str] = {}
    cover_meta_id = ""
    manifest: Dict[str, dict] = {}
    cover_href = ""

    for child in root:
        cname = _strip(child.tag)
        if cname == "metadata":
            for m in child:
                if _strip(m.tag) == "meta":
                    if m.attrib.get("refines") and m.attrib.get("property") == "title-type":
                        refinements[m.attrib["refines"].lstrip("#")] = (m.text or "").strip().lower()
                    elif m.attrib.get("name") == "cover" and m.attrib.get("content"):
                        cover_meta_id = m.attrib["content"]

    main_title = ""
    sub_title = ""
    for child in root:
        cname = _strip(child.tag)
        if cname == "metadata":
            for m in child:
                mname = _strip(m.tag)
                val = re.sub(r"\s+", " ", (m.text or "").strip())
                if not val:
                    continue
                if mname == "title":
                    ttype = refinements.get(m.attrib.get("id", ""), "")
                    if ttype == "main" and not main_title:
                        main_title = val
                    elif ttype in ("sub", "subtitle") and not sub_title:
                        sub_title = val
                    elif ttype in ("edition", "collection", "series"):
                        continue
                    else:
                        titles.append(val)
                elif mname == "creator" and val not in creators:
                    creators.append(val)
                elif mname == "contributor" and val not in contributors:
                    contributors.append(val)
                elif mname == "publisher" and not publisher:
                    publisher = val
        elif cname == "manifest":
            for item in child:
                if _strip(item.tag) == "item":
                    iid = item.attrib.get("id", "")
                    href = urllib.parse.unquote(item.attrib.get("href", "").split("#")[0])
                    mtype = item.attrib.get("media-type", "")
                    props = item.attrib.get("properties", "").split()
                    full_href = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else posixpath.normpath(href)
                    manifest[iid] = {"href": href, "full_href": full_href, "media_type": mtype}
                    if "cover-image" in props and not cover_href:
                        cover_href = full_href

    if not cover_href and cover_meta_id and cover_meta_id in manifest:
        cover_href = manifest[cover_meta_id]["full_href"]
    if not cover_href:
        for item in manifest.values():
            if item["media_type"].startswith("image/") and "cover" in item["href"].lower():
                cover_href = item["full_href"]
                break

    if main_title:
        full_title = f"{main_title}: {sub_title}" if (sub_title and sub_title.lower() not in main_title.lower()) else main_title
    elif len(titles) >= 2 and titles[1].lower() not in titles[0].lower() and not re.search(r"edition", titles[1], re.I):
        full_title = f"{titles[0]}: {titles[1]}"
    elif titles:
        full_title = titles[0]
    else:
        full_title = ""

    author_str = ", ".join(creators if creators else contributors[:2])
    cover_bytes = None
    if cover_href and cover_href in names:
        try:
            info = zf.getinfo(cover_href)
            if info.file_size <= 1_500_000:
                cover_bytes = zf.read(cover_href)
        except Exception:
            cover_bytes = None

    return full_title, author_str, publisher, cover_bytes, cover_href


def extract_remote_opf_and_cover(file_id: str, known_size: int = 0) -> Tuple[str, str, str, Optional[bytes], str]:
    """Reads OPF metadata and cover thumbnail from Drive using HTTP Range requests (>98% less bandwidth)."""
    token = get_drive_access_token()
    url = f"https://www.googleapis.com/drive/v3/files/{urllib.parse.quote(file_id)}?alt=media"
    headers = {"Authorization": f"Bearer {token}"}
    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        size = int(known_size or 0)
        if size <= 0:
            head = client.head(url, headers=headers)
            size = int(head.headers.get("content-length") or 0)
        if size <= 0:
            raise RuntimeError("Could not determine remote EPUB size for Range request")
        remote_f = _RangeHTTPFile(url, headers, size, client)
        with zipfile.ZipFile(remote_f, "r") as zf:
            return _extract_opf_and_cover_from_zip(zf)


def start_background_metadata_enrichment(limit: int = 5000):
    """Extracts true OPF title/author/publisher and cover thumbnails via HTTP Range requests without downloading full EPUBs."""
    global _META_ENRICH_RUNNING
    with _META_ENRICH_LOCK:
        if _META_ENRICH_RUNNING:
            return
        _META_ENRICH_RUNNING = True

    _db_write_lock = threading.Lock()
    stats = {"done": 0, "ok": 0, "err": 0, "total": 0}

    def _enrich_one(item: Tuple[str, int]):
        bid, known_size = item
        local_epub = BOOKS_DIR / bid / "book.epub"
        tmp_epub = Path(tempfile.gettempdir()) / f"sync_meta_{bid}.epub"
        try:
            t_val = a_val = p_val = cover_href = ""
            cover_bytes = None
            if local_epub.exists():
                with zipfile.ZipFile(local_epub, "r") as zf:
                    t_val, a_val, p_val, cover_bytes, cover_href = _extract_opf_and_cover_from_zip(zf)
            elif has_drive_oauth():
                try:
                    t_val, a_val, p_val, cover_bytes, cover_href = extract_remote_opf_and_cover(bid, known_size)
                except Exception:
                    # Fallback to full download only if Range request fails on a non-standard zip
                    drive_rest_download(bid, tmp_epub)
                    if tmp_epub.exists():
                        with zipfile.ZipFile(tmp_epub, "r") as zf:
                            t_val, a_val, p_val, cover_bytes, cover_href = _extract_opf_and_cover_from_zip(zf)
            elif os.path.exists(GDRIVE_BIN):
                dl_p = run_gdrive(["readonly", "download", bid, "--out", str(tmp_epub)], timeout=30)
                if dl_p.returncode == 0 and tmp_epub.exists():
                    with zipfile.ZipFile(tmp_epub, "r") as zf:
                        t_val, a_val, p_val, cover_bytes, cover_href = _extract_opf_and_cover_from_zip(zf)
            else:
                return

            saved_cover = 1 if (cover_bytes and save_cover_thumbnail(bid, cover_bytes, cover_href)) else 0
            with _db_write_lock:
                conn_w = get_db()
                try:
                    with conn_w:
                        conn_w.execute(
                            """
                            UPDATE books
                            SET title = COALESCE(NULLIF(?, ''), title),
                                author = COALESCE(NULLIF(?, ''), author),
                                publisher = COALESCE(NULLIF(?, ''), publisher),
                                cover_href = COALESCE(NULLIF(?, ''), cover_href),
                                has_cover = CASE WHEN ? = 1 THEN 1 ELSE COALESCE(has_cover, 0) END,
                                meta_verified = 1
                            WHERE id = ?
                            """,
                            (t_val, a_val, p_val, cover_href, saved_cover, bid),
                        )
                finally:
                    conn_w.close()
                stats["ok"] += 1
                if stats["ok"] <= 5:
                    print(f"OPF updated: {t_val} — {a_val} ({p_val})", flush=True)
        except Exception as exc:
            with _db_write_lock:
                stats["err"] += 1
                if stats["err"] <= 5:
                    print(f"OPF enrich error ({bid}): {exc}", flush=True)
        finally:
            with _db_write_lock:
                stats["done"] += 1
                if stats["done"] % 50 == 0 or stats["done"] == stats["total"]:
                    print(f"OPF enrichment: {stats['done']}/{stats['total']} (updated {stats['ok']}, errors {stats['err']})", flush=True)
            try:
                if tmp_epub.exists():
                    tmp_epub.unlink()
            except Exception:
                pass

    def _worker():
        global _META_ENRICH_RUNNING
        try:
            conn = get_db()
            try:
                rows = conn.execute(
                    "SELECT id, COALESCE(size, 0) AS size FROM books WHERE COALESCE(meta_verified, 0) = 0 OR publisher IS NULL OR publisher = '' ORDER BY downloaded DESC, modified_time DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                book_items = [(r["id"], int(r["size"] or 0)) for r in rows]
            finally:
                conn.close()

            if not book_items:
                print("OPF enrichment: all books already verified.", flush=True)
                return
            stats["total"] = len(book_items)
            print(f"OPF enrichment (Range ZIP) starting for {len(book_items)} unverified books...", flush=True)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(_enrich_one, book_items))
        finally:
            with _META_ENRICH_LOCK:
                _META_ENRICH_RUNNING = False

    threading.Thread(target=_worker, daemon=True, name="folio-meta-enrich").start()


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
    tmp_path = ""
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
            try:
                with conn:
                    conn.execute("UPDATE books SET annotations_drive_id = ? WHERE id = ?", (comp_id, book_id))
                    for a in anns:
                        conn.execute(
                            """
                            INSERT OR IGNORE INTO annotations
                            (id, book_id, chapter_index, chapter_href, chapter_title, page_number, selected_text, note, gemini_response, color, occurrence_index, created_at, updated_at)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                a["id"],
                                book_id,
                                a.get("chapter_index", 0),
                                a.get("chapter_href", ""),
                                a.get("chapter_title", ""),
                                a.get("page_number", ""),
                                a.get("selected_text", ""),
                                a.get("note", ""),
                                a.get("gemini_response", ""),
                                a.get("color", "yellow"),
                                a.get("occurrence_index", 0),
                                a.get("created_at", datetime.now(timezone.utc).isoformat()),
                                a.get("updated_at", datetime.now(timezone.utc).isoformat()),
                            ),
                        )
                        upsert_annotation_fts({**a, "book_id": book_id}, conn=conn)
            finally:
                conn.close()
    except Exception as e:
        print(f"Optional Drive annotation import notice: {e}", file=sys.stderr)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


def sync_annotations_to_drive(book_id: str) -> dict:
    """Uploads or updates companion .annotations.json and - Notes.md on Google Drive."""
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
        json_text = json.dumps(payload, indent=2, ensure_ascii=False)
        md_text = format_annotations_markdown(book, annotations)
        json_bytes = json_text.encode("utf-8")
        md_bytes = md_text.encode("utf-8")

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
        json_path.write_text(json_text, encoding="utf-8")
        md_path = tmp_dir / md_filename
        md_path.write_text(md_text, encoding="utf-8")

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
                drive_json_id = ""

        if not drive_json_id:
            cmd = ["mutate", "upload", str(json_path), "--mime-type", "application/json", "--json"]
            if parent_id:
                cmd.extend(["--parent", parent_id])
            up_proc = run_gdrive(cmd, timeout=60)
            if up_proc.returncode != 0:
                err_msg = up_proc.stderr.strip() or up_proc.stdout.strip()
                if "no credentials" in err_msg.lower() or "auth login" in err_msg.lower():
                    my_drive = None
                    cloud_dir = Path.home() / "Library" / "CloudStorage"
                    if cloud_dir.exists():
                        for gd in sorted(cloud_dir.glob("GoogleDrive-*")):
                            cand = gd / "My Drive"
                            if cand.is_dir():
                                my_drive = cand
                                break
                    if my_drive:
                        folder_rel = (book.get("folder_path") or "").strip("/")
                        target_dir = (my_drive / folder_rel) if (folder_rel and (my_drive / folder_rel).is_dir()) else (my_drive / "Folio Reading Notes")
                    else:
                        target_dir = BASE_DIR / "synced_notes"
                    target_dir.mkdir(parents=True, exist_ok=True)
                    (target_dir / json_filename).write_text(json_text, encoding="utf-8")
                    (target_dir / md_filename).write_text(md_text, encoding="utf-8")

                    now_iso = datetime.now(timezone.utc).isoformat()
                    conn = get_db()
                    try:
                        with conn:
                            conn.execute("UPDATE books SET last_synced_at = ? WHERE id = ?", (now_iso, book_id))
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


def export_notes_to_gdoc(book_id: str) -> dict:
    """Exports book highlights & notes as a Google Doc in Google Drive."""
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
    tmp_path = Path(tempfile.gettempdir()) / doc_name_md
    tmp_path.write_text(md_text, encoding="utf-8")

    cmd = ["mutate", "upload", str(tmp_path), "--mime-type", "application/vnd.google-apps.document", "--json"]
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
    def _loop():
        while True:
            time.sleep(interval_seconds)
            try:
                sync_dirty_books_to_drive()
            except Exception:
                pass

    t = threading.Thread(target=_loop, daemon=True, name="folio-periodic-drive-sync")
    t.start()
