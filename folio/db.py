"""SQLite database initialization, FTS5 full-text search, and settings helpers."""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from .config import BASE_DIR, DB_PATH, ensure_data_dirs, migrate_legacy_data_if_needed


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.execute("PRAGMA cache_size=-16000")
    return conn


def compute_progress_summary(
    manifest: Optional[dict], last_chapter_index: int, scroll_percent: float
) -> Tuple[int, int, str, int]:
    """Returns (chapter_count, total_words, last_chapter_title, progress_percent)."""
    chapters = (manifest or {}).get("chapters") or []
    ch_count = len(chapters)
    if ch_count == 0:
        return 0, 0, f"Sec. {max(0, last_chapter_index) + 1}", int(round(max(0.0, min(1.0, scroll_percent)) * 100))

    idx = max(0, min(ch_count - 1, int(last_chapter_index or 0)))
    ch_title = chapters[idx].get("title") or f"Sec. {idx + 1}"
    total_words = sum(int(c.get("word_count") or 0) for c in chapters)
    sp = max(0.0, min(1.0, float(scroll_percent or 0.0)))

    if total_words > 0:
        words_before = sum(int(chapters[i].get("word_count") or 0) for i in range(idx))
        cur_words = int(chapters[idx].get("word_count") or 0)
        pct = min(100, int(round(((words_before + cur_words * sp) / total_words) * 100)))
    else:
        pct = min(100, int(round(((idx + sp) / ch_count) * 100)))
    return ch_count, total_words, ch_title, pct


def init_db():
    ensure_data_dirs()
    migrate_legacy_data_if_needed()

    conn = get_db()
    try:
        with conn:
            conn.execute("PRAGMA journal_mode=WAL")
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

                CREATE TABLE IF NOT EXISTS argument_maps (
                    book_id TEXT NOT NULL,
                    chapter_index INTEGER NOT NULL,
                    map_json TEXT NOT NULL,
                    model TEXT DEFAULT '',
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (book_id, chapter_index)
                );

                CREATE TABLE IF NOT EXISTS hidden_books (
                    id TEXT PRIMARY KEY,
                    name TEXT DEFAULT '',
                    title TEXT DEFAULT '',
                    author TEXT DEFAULT '',
                    book_json TEXT DEFAULT '',
                    hidden_at TEXT NOT NULL
                );
                """
            )

            # Schema migrations for books & annotations
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(books)").fetchall()}
            if "folder_path" not in cols:
                conn.execute("ALTER TABLE books ADD COLUMN folder_path TEXT DEFAULT ''")
            if "tags" not in cols:
                conn.execute("ALTER TABLE books ADD COLUMN tags TEXT DEFAULT '[]'")
            if "meta_verified" not in cols:
                conn.execute("ALTER TABLE books ADD COLUMN meta_verified INTEGER DEFAULT 0")
            if "chapter_count" not in cols:
                conn.execute("ALTER TABLE books ADD COLUMN chapter_count INTEGER DEFAULT 0")
            if "total_words" not in cols:
                conn.execute("ALTER TABLE books ADD COLUMN total_words INTEGER DEFAULT 0")
            if "last_chapter_title" not in cols:
                conn.execute("ALTER TABLE books ADD COLUMN last_chapter_title TEXT DEFAULT ''")
            if "progress_percent" not in cols:
                conn.execute("ALTER TABLE books ADD COLUMN progress_percent INTEGER DEFAULT 0")
            if "has_cover" not in cols:
                conn.execute("ALTER TABLE books ADD COLUMN has_cover INTEGER DEFAULT 0")

            ann_cols = {r["name"] for r in conn.execute("PRAGMA table_info(annotations)").fetchall()}
            if "page_number" not in ann_cols:
                conn.execute("ALTER TABLE annotations ADD COLUMN page_number TEXT DEFAULT ''")

            # Secondary indexes
            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_annotations_book_ch ON annotations(book_id, chapter_index);
                CREATE INDEX IF NOT EXISTS idx_chats_book_created ON chats(book_id, created_at);
                CREATE INDEX IF NOT EXISTS idx_bookmarks_book ON bookmarks(book_id);
                CREATE INDEX IF NOT EXISTS idx_books_opened ON books(last_opened_at DESC);
                CREATE INDEX IF NOT EXISTS idx_books_downloaded ON books(downloaded);
                """
            )

            # FTS5 virtual tables for instant full-text search
            conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS chapter_text_fts USING fts5(
                    book_id UNINDEXED,
                    chapter_index UNINDEXED,
                    chapter_title,
                    content,
                    tokenize='unicode61'
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS annotations_fts USING fts5(
                    ann_id UNINDEXED,
                    book_id UNINDEXED,
                    selected_text,
                    note,
                    gemini_response,
                    tokenize='unicode61'
                );
                """
            )

            # One-time backfill of precomputed progress summary columns from manifest_json
            if not conn.execute("SELECT 1 FROM settings WHERE key = 'progress_summary_backfill_v1'").fetchone():
                rows = conn.execute(
                    "SELECT id, manifest_json, last_chapter_index, scroll_percent FROM books WHERE manifest_json IS NOT NULL"
                ).fetchall()
                for r in rows:
                    try:
                        manifest = json.loads(r["manifest_json"])
                    except Exception:
                        manifest = None
                    ch_cnt, tot_words, ch_title, pct = compute_progress_summary(
                        manifest, r["last_chapter_index"] or 0, r["scroll_percent"] or 0.0
                    )
                    conn.execute(
                        "UPDATE books SET chapter_count = ?, total_words = ?, last_chapter_title = ?, progress_percent = ? WHERE id = ?",
                        (ch_cnt, tot_words, ch_title, pct, r["id"]),
                    )
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('progress_summary_backfill_v1', '1')")

            # One-time backfill of annotations_fts
            if not conn.execute("SELECT 1 FROM settings WHERE key = 'annotations_fts_backfill_v1'").fetchone():
                conn.execute("DELETE FROM annotations_fts")
                ann_rows = conn.execute(
                    "SELECT id, book_id, selected_text, note, gemini_response FROM annotations"
                ).fetchall()
                conn.executemany(
                    "INSERT INTO annotations_fts (ann_id, book_id, selected_text, note, gemini_response) VALUES (?, ?, ?, ?, ?)",
                    [(r["id"], r["book_id"], r["selected_text"] or "", r["note"] or "", r["gemini_response"] or "") for r in ann_rows],
                )
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('annotations_fts_backfill_v1', '1')")

            # Stamp-gated seed loading
            seed_file = BASE_DIR / "tag_seed.json"
            if seed_file.exists():
                try:
                    st = seed_file.stat()
                    stamp = f"{int(st.st_mtime)}:{st.st_size}"
                    prev_stamp = conn.execute("SELECT value FROM settings WHERE key = 'seed_tags_stamp'").fetchone()
                    if not prev_stamp or prev_stamp["value"] != stamp:
                        seed_map = json.loads(seed_file.read_text(encoding="utf-8"))
                        for bid, tags in seed_map.items():
                            if bid and tags:
                                conn.execute(
                                    "UPDATE books SET tags = ? WHERE id = ? AND (tags IS NULL OR tags = '' OR tags = '[]')",
                                    (json.dumps(tags), bid),
                                )
                        conn.execute(
                            "INSERT OR REPLACE INTO settings (key, value) VALUES ('seed_tags_stamp', ?)",
                            (stamp,),
                        )
                except Exception:
                    pass

            conn.execute(
                "DELETE FROM books WHERE LOWER(name) LIKE '%getting started with adobe digital editions%' OR LOWER(title) LIKE '%getting started with adobe digital editions%' OR id = '1Xx-8moh8UVB9Y_BVTNBkR2uLIbhIjvG0' OR LOWER(name) LIKE '%process-tracing methods%' OR parent_folder_id = '1nl6-80t4lZ7Yg8LDjaujJOiHOQ1NlbhX' OR LOWER(folder_path) LIKE '%_duplicates_to_trash%'"
            )

            meta_file = BASE_DIR / "metadata_seed.json"
            if meta_file.exists():
                try:
                    st = meta_file.stat()
                    stamp = f"{int(st.st_mtime)}:{st.st_size}"
                    prev_stamp = conn.execute("SELECT value FROM settings WHERE key = 'seed_meta_stamp'").fetchone()
                    if not prev_stamp or prev_stamp["value"] != stamp:
                        meta_map = json.loads(meta_file.read_text(encoding="utf-8"))
                        for bid, m in meta_map.items():
                            if not bid or not isinstance(m, dict):
                                continue
                            fp = m.get("folder_path", "")
                            if fp:
                                conn.execute(
                                    "UPDATE books SET folder_path = ? WHERE id = ? AND (folder_path IS NULL OR folder_path = '')",
                                    (fp, bid),
                                )
                        conn.execute(
                            "INSERT OR REPLACE INTO settings (key, value) VALUES ('seed_meta_stamp', ?)",
                            (stamp,),
                        )
                except Exception:
                    pass

            reverify_row = conn.execute("SELECT value FROM settings WHERE key = 'opf_reverify_v2'").fetchone()
            if not reverify_row:
                conn.execute("UPDATE books SET meta_verified = 0")
                conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('opf_reverify_v2', '1')")
    finally:
        conn.close()


def index_book_chapters_fts(book_id: str, chapter_texts: Iterable[Tuple[int, str, str]], conn: Optional[sqlite3.Connection] = None):
    """Replaces all FTS5 chapter text entries for a given book."""
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    try:
        rows = [(book_id, idx, title or "", text or "") for idx, title, text in chapter_texts if text]
        if own_conn:
            with conn:
                conn.execute("DELETE FROM chapter_text_fts WHERE book_id = ?", (book_id,))
                if rows:
                    conn.executemany(
                        "INSERT INTO chapter_text_fts (book_id, chapter_index, chapter_title, content) VALUES (?, ?, ?, ?)",
                        rows,
                    )
        else:
            conn.execute("DELETE FROM chapter_text_fts WHERE book_id = ?", (book_id,))
            if rows:
                conn.executemany(
                    "INSERT INTO chapter_text_fts (book_id, chapter_index, chapter_title, content) VALUES (?, ?, ?, ?)",
                    rows,
                )
    finally:
        if own_conn:
            conn.close()


def upsert_annotation_fts(ann: dict, conn: Optional[sqlite3.Connection] = None):
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    try:
        params = (
            ann["id"],
            ann["book_id"],
            ann.get("selected_text") or "",
            ann.get("note") or "",
            ann.get("gemini_response") or "",
        )
        if own_conn:
            with conn:
                conn.execute("DELETE FROM annotations_fts WHERE ann_id = ?", (ann["id"],))
                conn.execute(
                    "INSERT INTO annotations_fts (ann_id, book_id, selected_text, note, gemini_response) VALUES (?, ?, ?, ?, ?)",
                    params,
                )
        else:
            conn.execute("DELETE FROM annotations_fts WHERE ann_id = ?", (ann["id"],))
            conn.execute(
                "INSERT INTO annotations_fts (ann_id, book_id, selected_text, note, gemini_response) VALUES (?, ?, ?, ?, ?)",
                params,
            )
    finally:
        if own_conn:
            conn.close()


def delete_annotation_fts(ann_id: str, conn: Optional[sqlite3.Connection] = None):
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    try:
        if own_conn:
            with conn:
                conn.execute("DELETE FROM annotations_fts WHERE ann_id = ?", (ann_id,))
        else:
            conn.execute("DELETE FROM annotations_fts WHERE ann_id = ?", (ann_id,))
    finally:
        if own_conn:
            conn.close()


def build_fts_query(raw_query: str) -> str:
    """Converts a user search string into a safe FTS5 MATCH query."""
    tokens = re.findall(r"\w+", raw_query.strip(), flags=re.UNICODE)
    if not tokens:
        return ""
    # Prefix-match each token with implicit AND for responsive scholarly search
    return " ".join(f'"{t}"*' for t in tokens[:12])


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
    db_key = get_setting("gemini_api_key", "").strip()
    if db_key:
        return db_key
    for env_var in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        val = os.environ.get(env_var, "").strip()
        if val:
            return val
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
