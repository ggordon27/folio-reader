"""Configuration, paths, constants, and one-time local data migration."""

from __future__ import annotations

import mimetypes
import os
import shutil
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
LEGACY_DATA_DIR = BASE_DIR / "data"


def _resolve_data_dir() -> Path:
    env_dir = os.environ.get("FOLIO_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser()
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Folio"
    return LEGACY_DATA_DIR


DATA_DIR = _resolve_data_dir()
BOOKS_DIR = DATA_DIR / "books"
COVERS_DIR = DATA_DIR / "covers"
DB_PATH = DATA_DIR / "reader.db"

GDRIVE_BIN = shutil.which("gdrive") or "/Users/dr_geoff/.gemini/antigravity/bin/gdrive"
GEMINI_API_BASE = "https://generativelanguage.googleapis.com/v1/interactions"

# Ensure extra mime types
mimetypes.add_type("application/xhtml+xml", ".xhtml")
mimetypes.add_type("application/epub+zip", ".epub")
mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("font/ttf", ".ttf")
mimetypes.add_type("font/otf", ".otf")

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

IGNORED_FOLDER_IDS = {
    "1jN3X4CNW5vpvNyu47TR32kUJFPmTZRba",  # ElevenReader epubs
    "1pY3dK-t0C9_5v9hcIuzeyCNLN3Oog6oM",  # tts-epub converter2/output_epubs
    "18ilVVqJHmQa5y-96tCN40uY5uCP7Oj2W",  # tts-epub converter2
    "1nl6-80t4lZ7Yg8LDjaujJOiHOQ1NlbhX",  # _Duplicates_To_Trash
}
IGNORED_FOLDER_NAMES = {
    "elevenreader epubs",
    "elevenreader",
    "epub_out",
    "output_epubs",
    "tts-epub",
    "_duplicates_to_trash",
}


def ensure_data_dirs():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    BOOKS_DIR.mkdir(parents=True, exist_ok=True)
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    STATIC_DIR.mkdir(parents=True, exist_ok=True)


def migrate_legacy_data_if_needed():
    """
    One-time migration from repo-local ./data to ~/Library/Application Support/Folio
    when running locally on macOS. Copies reader.db, book.epub archives (skipping legacy
    extracted/ folders), and helper JSON maps.
    """
    ensure_data_dirs()
    if DATA_DIR.resolve() == LEGACY_DATA_DIR.resolve():
        return
    if DB_PATH.exists():
        return
    legacy_db = LEGACY_DATA_DIR / "reader.db"
    if not legacy_db.exists():
        return

    print(f"Migrating local Folio data from {LEGACY_DATA_DIR} -> {DATA_DIR} ...", flush=True)
    shutil.copy2(legacy_db, DB_PATH)
    for json_name in ("folder_map.json", "ignored_file_ids.json", "drive_epubs.json"):
        src = LEGACY_DATA_DIR / json_name
        if src.exists():
            shutil.copy2(src, DATA_DIR / json_name)

    legacy_books = LEGACY_DATA_DIR / "books"
    if legacy_books.is_dir():
        copied = 0
        for bdir in legacy_books.iterdir():
            if not bdir.is_dir():
                continue
            epub_src = bdir / "book.epub"
            if epub_src.is_file():
                dst_dir = BOOKS_DIR / bdir.name
                dst_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(epub_src, dst_dir / "book.epub")
                copied += 1
        print(f"Migrated reader.db and {copied} cached EPUB archive(s) (skipped legacy extracted/ folders).", flush=True)
