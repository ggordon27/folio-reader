#!/usr/bin/env python3
"""
Parses book filenames to extract clean Title Case book titles and authors,
and uses Gemini to resolve authors & full titles for all books in SQLite.
"""
from pathlib import Path
import json
import re
import sqlite3
import sys

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
import server

DB_PATH = str(BASE_DIR / "data" / "reader.db")

SMALL_WORDS = {
    "a", "an", "and", "as", "at", "but", "by", "for", "from", "in", "into",
    "nor", "of", "on", "onto", "or", "over", "per", "so", "the", "to", "up",
    "via", "with", "yet",
}


def smart_title_case(s: str) -> str:
    words = s.split()
    out = []
    for i, w in enumerate(words):
        low = w.lower()
        if i > 0 and i < len(words) - 1 and low in SMALL_WORDS:
            out.append(low)
        elif w.isupper() and len(w) > 1:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:])
    return " ".join(out)


def parse_filename_heuristics(filename: str) -> tuple[str, str]:
    stem = re.sub(r"\.epub$", "", filename, flags=re.I).strip()
    # Strip trailing timestamp like _26_11_07_12_20_25
    stem = re.sub(r"_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}$", "", stem)
    # Strip _for_geoffrey-gordon
    stem = re.sub(r"_for_geoffrey[-_]gordon$", "", stem, flags=re.I)
    # Strip (TTS) or (ElevenReader)
    stem = re.sub(r"\s*\((?:TTS|ElevenReader)\)\s*$", "", stem, flags=re.I)
    stem = re.sub(r"[-_\s]+elevenreader(?:\s+edition)?$", "", stem, flags=re.I)

    # Pattern 1: Anna's Archive "Title -- Author -- Year -- Publisher -- ..."
    if " -- " in stem:
        parts = [p.strip() for p in stem.split(" -- ")]
        if len(parts) >= 2:
            return parts[0], parts[1]

    # Pattern 2: "Author - Year - Title - Subtitle"
    m_ay = re.match(r"^([A-Z][^-]+?)\s+-\s+(?:19|20)\d{2}\s+-\s+(.+)$", stem)
    if m_ay:
        author = m_ay.group(1).strip()
        title_raw = m_ay.group(2).replace(" - ", ": ").strip()
        return title_raw, author

    # Pattern 3: "Author - Title - Subtitle" (3 parts separated by ' - ')
    if " - " in stem:
        parts = [p.strip() for p in stem.split(" - ") if p.strip()]
        if len(parts) == 3:
            # Check if part 0 looks like a person's name (1-4 words)
            if len(parts[0].split()) <= 4:
                return f"{parts[1]}: {parts[2]}", parts[0]
        elif len(parts) == 2:
            # Could be "Title - Author" or "Author - Title"
            p0_words = parts[0].split()
            p1_words = parts[1].split()
            if len(p1_words) <= 3 and not any(w.lower() in SMALL_WORDS for w in p1_words):
                return parts[0], parts[1]
            elif len(p0_words) <= 3 and not any(w.lower() in SMALL_WORDS for w in p0_words):
                return parts[1], parts[0]

    # Clean hyphens/underscores and apply smart Title Case
    cleaned = stem.replace("_", " ").replace("-", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return smart_title_case(cleaned), ""


def apply_heuristics_to_db():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, name, title, author, downloaded FROM books").fetchall()
    updated = 0
    with conn:
        for r in rows:
            if r["downloaded"] and r["author"]:
                continue
            h_title, h_author = parse_filename_heuristics(r["name"])
            cur_author = (r["author"] or "").strip()
            if not cur_author:
                conn.execute(
                    "UPDATE books SET title = ?, author = ? WHERE id = ?",
                    (h_title, h_author, r["id"]),
                )
                updated += 1
    conn.close()
    print(f"Heuristics updated {updated} books.")


if __name__ == "__main__":
    apply_heuristics_to_db()
