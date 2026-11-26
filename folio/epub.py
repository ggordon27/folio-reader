"""ZIP-direct EPUB parsing, cached resource serving, and SQLite FTS5 search."""

from __future__ import annotations

import html as html_mod
import mimetypes
import posixpath
import re
import sys
import threading
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import BOOKS_DIR
from .db import build_fts_query, get_db, index_book_chapters_fts

_RESOURCE_CACHE_LOCK = threading.Lock()
_RESOURCE_CACHE: "OrderedDict[Tuple[str, str], Tuple[bytes, str]]" = OrderedDict()
_RESOURCE_CACHE_MAX = 128

_SELF_CLOSING_RE = re.compile(
    r"<(a|span|div|p|sup|sub|em|i|b|strong|small|label)((?:\s+[^<>\"']*(?:\"[^\"]*\"|'[^']*'))*)\s*/>",
    flags=re.IGNORECASE,
)


def strip_ns(tag: str) -> str:
    if "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def _clean_text(s: str) -> str:
    """Decode HTML entities and collapse whitespace in extracted metadata/TOC strings."""
    if not s:
        return ""
    return re.sub(r"\s+", " ", html_mod.unescape(s)).strip()


def extract_plain_text_from_xhtml(raw_bytes: bytes) -> str:
    try:
        text = raw_bytes.decode("utf-8", errors="replace")
    except Exception:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"</?(p|div|h[1-6]|li|tr|blockquote|section|article|br|hr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = html_mod.unescape(text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def _normalize_zip_rel_path(rel_path: str) -> Optional[str]:
    cleaned = rel_path.replace("\\", "/").lstrip("/")
    norm = posixpath.normpath(cleaned)
    if norm in ("", ".") or norm.startswith("..") or "/../" in norm:
        return None
    return norm


def _build_zip_lookup(zf: zipfile.ZipFile) -> Dict[str, str]:
    """Maps normalized & unquoted paths to exact zip entry names."""
    lookup: Dict[str, str] = {}
    for name in zf.namelist():
        norm = posixpath.normpath(name.replace("\\", "/")).lstrip("/")
        lookup[norm] = name
        unq = urllib.parse.unquote(norm)
        if unq not in lookup:
            lookup[unq] = name
    return lookup


def invalidate_epub_cache(book_id: Optional[str] = None):
    with _RESOURCE_CACHE_LOCK:
        if book_id is None:
            _RESOURCE_CACHE.clear()
        else:
            for k in [k for k in _RESOURCE_CACHE if k[0] == book_id]:
                _RESOURCE_CACHE.pop(k, None)


def read_epub_resource(book_id: str, rel_path: str) -> Optional[Tuple[bytes, str]]:
    """
    Reads an EPUB resource directly from book.epub (no extracted/ directory needed),
    normalizes self-closing XHTML tags once for HTML/XHTML, and caches in an LRU.
    """
    norm_path = _normalize_zip_rel_path(rel_path)
    if not norm_path:
        return None

    cache_key = (book_id, norm_path)
    with _RESOURCE_CACHE_LOCK:
        cached = _RESOURCE_CACHE.get(cache_key)
        if cached is not None:
            _RESOURCE_CACHE.move_to_end(cache_key)
            return cached

    epub_path = BOOKS_DIR / book_id / "book.epub"
    raw: Optional[bytes] = None
    if epub_path.is_file():
        try:
            with zipfile.ZipFile(epub_path, "r") as zf:
                lookup = _build_zip_lookup(zf)
                entry_name = lookup.get(norm_path) or lookup.get(urllib.parse.unquote(norm_path))
                if entry_name:
                    raw = zf.read(entry_name)
        except Exception:
            raw = None

    # Fallback to legacy extracted/ if book.epub is absent
    if raw is None:
        extract_root = (BOOKS_DIR / book_id / "extracted").resolve()
        target_file = (extract_root / norm_path).resolve()
        if target_file.is_relative_to(extract_root) and target_file.is_file():
            raw = target_file.read_bytes()

    if raw is None:
        return None

    ctype, _ = mimetypes.guess_type(norm_path)
    if not ctype:
        ctype = "application/octet-stream"

    suffix = Path(norm_path).suffix.lower()
    if suffix in (".xhtml", ".html", ".htm"):
        ctype = "text/html; charset=utf-8"
        text = raw.decode("utf-8", errors="replace")
        text = _SELF_CLOSING_RE.sub(
            lambda m: f"<{m.group(1)}{m.group(2) or ''}></{m.group(1)}>",
            text,
        )
        raw = text.encode("utf-8")

    # Cache resources up to 2 MB each to keep memory bounded
    if len(raw) <= 2 * 1024 * 1024:
        with _RESOURCE_CACHE_LOCK:
            _RESOURCE_CACHE[cache_key] = (raw, ctype)
            _RESOURCE_CACHE.move_to_end(cache_key)
            while len(_RESOURCE_CACHE) > _RESOURCE_CACHE_MAX:
                _RESOURCE_CACHE.popitem(last=False)

    return raw, ctype


def parse_epub_from_zip(epub_path: Path) -> Tuple[dict, List[Tuple[int, str, str]]]:
    """
    Parses container.xml, OPF metadata, manifest, spine, TOC, and chapter plain text
    directly from the EPUB ZIP archive without extracting files to disk.
    Returns (manifest_dict, chapter_plain_texts).
    """
    with zipfile.ZipFile(epub_path, "r") as zf:
        lookup = _build_zip_lookup(zf)

        def read_zip_entry(rel: str) -> Optional[bytes]:
            norm = _normalize_zip_rel_path(rel)
            if not norm:
                return None
            entry = lookup.get(norm) or lookup.get(urllib.parse.unquote(norm))
            if not entry:
                return None
            try:
                return zf.read(entry)
            except Exception:
                return None

        opf_rel_path = None
        container_bytes = read_zip_entry("META-INF/container.xml")
        if container_bytes:
            try:
                tree = ET.fromstring(container_bytes)
                for elem in tree.iter():
                    if strip_ns(elem.tag) == "rootfile":
                        opf_rel_path = elem.attrib.get("full-path")
                        if opf_rel_path:
                            break
            except Exception:
                pass

        if not opf_rel_path:
            opf_candidates = [n for n in zf.namelist() if n.lower().endswith(".opf")]
            if not opf_candidates:
                raise ValueError("Could not find package.opf in EPUB archive")
            opf_rel_path = opf_candidates[0]

        opf_bytes = read_zip_entry(opf_rel_path)
        if not opf_bytes:
            raise ValueError(f"Could not read OPF file: {opf_rel_path}")

        opf_dir = posixpath.dirname(opf_rel_path.replace("\\", "/"))

        def resolve_href(href: str) -> str:
            if not href:
                return ""
            clean = urllib.parse.unquote(href.split("#")[0])
            if opf_dir:
                return posixpath.normpath(posixpath.join(opf_dir, clean))
            return posixpath.normpath(clean)

        root = ET.fromstring(opf_bytes)

        title = ""
        subtitle = ""
        creators: List[str] = []
        publisher = ""
        identifier = ""
        language = "en"
        cover_meta_id = ""

        manifest: Dict[str, dict] = {}
        spine_ids: List[str] = []
        nav_href = ""
        ncx_href = ""
        cover_href = ""

        for child in root:
            cname = strip_ns(child.tag)
            if cname == "metadata":
                for m in child:
                    mname = strip_ns(m.tag)
                    text_val = _clean_text(m.text or "")
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

        toc_entries: List[dict] = []
        href_to_title: Dict[str, str] = {}

        if nav_href:
            nav_bytes = read_zip_entry(nav_href)
            if nav_bytes:
                try:
                    nav_dir = posixpath.dirname(nav_href)
                    nav_tree = ET.fromstring(nav_bytes)
                    for nav_el in nav_tree.iter():
                        if strip_ns(nav_el.tag) == "nav":
                            attrib_str = " ".join(f"{k}={v}" for k, v in nav_el.attrib.items()).lower()
                            if "toc" in attrib_str or not toc_entries:
                                for a_el in nav_el.iter():
                                    if strip_ns(a_el.tag) == "a" and a_el.attrib.get("href"):
                                        raw_href = a_el.attrib["href"]
                                        label = _clean_text("".join(a_el.itertext()))
                                        base_part = urllib.parse.unquote(raw_href.split("#")[0])
                                        frag = raw_href.split("#")[1] if "#" in raw_href else ""
                                        resolved = (
                                            posixpath.normpath(posixpath.join(nav_dir, base_part))
                                            if base_part
                                            else nav_href
                                        )
                                        if label:
                                            toc_entries.append({"label": label, "href": resolved, "fragment": frag})
                                            if resolved not in href_to_title:
                                                href_to_title[resolved] = label
                                if toc_entries:
                                    break
                except Exception as e:
                    print(f"Warning parsing nav_href: {e}", file=sys.stderr)

        if not toc_entries and ncx_href:
            ncx_bytes = read_zip_entry(ncx_href)
            if ncx_bytes:
                try:
                    ncx_dir = posixpath.dirname(ncx_href)
                    ncx_tree = ET.fromstring(ncx_bytes)
                    for np in ncx_tree.iter():
                        if strip_ns(np.tag) == "navPoint":
                            label = ""
                            src = ""
                            for sub in np:
                                sname = strip_ns(sub.tag)
                                if sname == "navLabel":
                                    label = _clean_text("".join(sub.itertext()))
                                elif sname == "content":
                                    src = sub.attrib.get("src", "")
                            if label and src:
                                base_part = urllib.parse.unquote(src.split("#")[0])
                                frag = src.split("#")[1] if "#" in src else ""
                                resolved = posixpath.normpath(posixpath.join(ncx_dir, base_part))
                                toc_entries.append({"label": label, "href": resolved, "fragment": frag})
                                if resolved not in href_to_title:
                                    href_to_title[resolved] = label
                except Exception as e:
                    print(f"Warning parsing ncx_href: {e}", file=sys.stderr)

        chapters: List[dict] = []
        chapter_plain_texts: List[Tuple[int, str, str]] = []

        for idx, item_id in enumerate(spine_ids):
            item = manifest[item_id]
            full_href = item["full_href"]
            ch_title = href_to_title.get(full_href, "")
            word_count = 0
            plain = ""
            raw_bytes = read_zip_entry(full_href)
            if raw_bytes:
                plain = extract_plain_text_from_xhtml(raw_bytes)
                word_count = len(plain.split())
                if not ch_title:
                    try:
                        txt_html = raw_bytes.decode("utf-8", errors="replace")
                        m_h = re.search(r"<h[1-3][^>]*>(.*?)</h[1-3]>", txt_html, flags=re.DOTALL | re.IGNORECASE)
                        if m_h:
                            t_cand = _clean_text(re.sub(r"<[^>]+>", "", m_h.group(1)))
                            if t_cand:
                                ch_title = t_cand
                        if not ch_title:
                            m_t = re.search(r"<title[^>]*>(.*?)</title>", txt_html, flags=re.DOTALL | re.IGNORECASE)
                            if m_t:
                                t_cand = _clean_text(re.sub(r"<[^>]+>", "", m_t.group(1)))
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
            if plain:
                chapter_plain_texts.append((idx, ch_title, plain))

        href_to_idx = {ch["href"]: ch["index"] for ch in chapters}
        for entry in toc_entries:
            entry["chapter_index"] = href_to_idx.get(entry["href"], 0)

        full_title = (
            f"{title}: {subtitle}"
            if (title and subtitle and subtitle.lower() not in title.lower())
            else (title or epub_path.stem)
        )

        manifest_out = {
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
        return manifest_out, chapter_plain_texts


def get_chapter_plain_text(book_id: str, chapter: dict) -> Tuple[str, str]:
    href = chapter.get("href", "")
    title = chapter.get("title", "")
    res = read_epub_resource(book_id, href)
    if not res:
        return title, ""
    raw, _ = res
    return title, extract_plain_text_from_xhtml(raw)


def ensure_book_fts_indexed(book_id: str):
    """Lazily populates chapter_text_fts for a cached book if not yet indexed."""
    conn = get_db()
    try:
        has_rows = conn.execute(
            "SELECT 1 FROM chapter_text_fts WHERE book_id = ? LIMIT 1", (book_id,)
        ).fetchone()
        if has_rows:
            return
    finally:
        conn.close()

    epub_path = BOOKS_DIR / book_id / "book.epub"
    if not epub_path.is_file():
        return
    try:
        _, chapter_plain_texts = parse_epub_from_zip(epub_path)
        index_book_chapters_fts(book_id, chapter_plain_texts)
    except Exception as e:
        print(f"FTS5 lazy index warning ({book_id}): {e}", file=sys.stderr)


def search_book_passages_fts(book_id: str, query: str, max_results: int = 8) -> List[dict]:
    """Fast FTS5 search across chapters of a single book with BM25 ranking."""
    fts_q = build_fts_query(query)
    if not fts_q:
        return []
    ensure_book_fts_indexed(book_id)
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT chapter_index,
                   chapter_title,
                   snippet(chapter_text_fts, 3, '', '', '...', 48) AS snippet,
                   -bm25(chapter_text_fts) AS score
            FROM chapter_text_fts
            WHERE book_id = ? AND chapter_text_fts MATCH ?
            ORDER BY bm25(chapter_text_fts)
            LIMIT ?
            """,
            (book_id, fts_q, max_results),
        ).fetchall()
        return [
            {
                "chapter_index": int(r["chapter_index"]),
                "chapter_title": r["chapter_title"],
                "snippet": r["snippet"],
                "score": float(r["score"]),
            }
            for r in rows
        ]
    except Exception:
        return []
    finally:
        conn.close()


def search_all_books_and_notes(query: str, max_results: int = 30) -> dict:
    """Instant FTS5 search across all saved annotations and all cached books."""
    q = query.strip()
    if not q:
        return {"annotation_matches": [], "book_matches": []}

    fts_q = build_fts_query(q)
    conn = get_db()
    try:
        downloaded_ids = [
            r["id"]
            for r in conn.execute("SELECT id FROM books WHERE downloaded = 1").fetchall()
        ]
        indexed_ids = {
            r["book_id"]
            for r in conn.execute("SELECT DISTINCT book_id FROM chapter_text_fts").fetchall()
        }
    finally:
        conn.close()

    # Lazily index any previously cached books that aren't in FTS5 yet
    for bid in downloaded_ids:
        if bid not in indexed_ids:
            ensure_book_fts_indexed(bid)

    conn = get_db()
    try:
        ann_rows = []
        if fts_q:
            ann_rows = conn.execute(
                """
                SELECT a.*, b.title AS book_title, b.author AS book_author, b.name AS book_name
                FROM annotations_fts f
                JOIN annotations a ON a.id = f.ann_id
                JOIN books b ON a.book_id = b.id
                WHERE annotations_fts MATCH ?
                ORDER BY bm25(annotations_fts)
                LIMIT ?
                """,
                (fts_q, max_results),
            ).fetchall()

        if not ann_rows:
            like_q = f"%{q}%"
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

        book_matches = []
        if fts_q:
            bm_rows = conn.execute(
                """
                SELECT f.book_id,
                       b.title AS book_title,
                       b.author AS book_author,
                       b.name AS book_name,
                       f.chapter_index,
                       f.chapter_title,
                       snippet(chapter_text_fts, 3, '', '', '...', 48) AS snippet,
                       -bm25(chapter_text_fts) AS score
                FROM chapter_text_fts f
                JOIN books b ON b.id = f.book_id
                WHERE chapter_text_fts MATCH ?
                ORDER BY bm25(chapter_text_fts)
                LIMIT ?
                """,
                (fts_q, max_results),
            ).fetchall()
            book_matches = [
                {
                    "book_id": r["book_id"],
                    "book_title": r["book_title"] or r["book_name"],
                    "book_author": r["book_author"] or "",
                    "chapter_index": int(r["chapter_index"]),
                    "chapter_title": r["chapter_title"],
                    "snippet": r["snippet"],
                    "score": float(r["score"]),
                }
                for r in bm_rows
            ]

        return {
            "annotation_matches": [dict(r) for r in ann_rows],
            "book_matches": book_matches,
        }
    finally:
        conn.close()
