#!/usr/bin/env python3
"""
Batch-tags (Root) books in data/reader.db with 1-2 cross-cutting theme tags
using the Gemini Interactions API, and writes tag_seed.json for Railway sync.
"""
import json
import re
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(BASE_DIR))
import server

DB_PATH = BASE_DIR / "data" / "reader.db"
SEED_PATH = BASE_DIR / "tag_seed.json"

ALLOWED_TAGS = [
    "Value & Capital",
    "Energy & Ecology",
    "Race & Empire",
    "Social Reproduction",
    "Critical Theory",
    "Tech & Logistics",
    "Strategy & History",
    "Fiction",
]


def run_autotag():
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT id, title, author, name, tags
        FROM books
        WHERE (folder_path IS NULL OR folder_path = '')
          AND (tags IS NULL OR tags = '' OR tags = '[]')
        ORDER BY modified_time DESC
        """
    ).fetchall()

    print(f"Found {len(rows)} untagged (Root) books.", flush=True)
    seed_map = {}
    if SEED_PATH.exists():
        try:
            seed_map = json.loads(SEED_PATH.read_text(encoding="utf-8"))
        except Exception:
            seed_map = {}

    batch_size = 60
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        lines = [
            f"{r['id']}\t{r['title'] or r['name']}\t{r['author'] or 'Unknown'}"
            for r in batch
        ]
        prompt = (
            "You are a scholarly librarian. Assign 1 or 2 tags from the ALLOWED TAGS list below to each book.\n"
            f"ALLOWED TAGS: {', '.join(ALLOWED_TAGS)}\n\n"
            "Rules:\n"
            "- Use ONLY exact strings from ALLOWED TAGS.\n"
            "- Assign 1-2 most relevant tags per book (2 only when clearly bridging two themes).\n"
            "- Return ONLY a raw JSON array of objects: [{\"id\": \"...\", \"tags\": [\"...\"]}]\n\n"
            "BOOKS (id\\ttitle\\tauthor):\n"
            + "\n".join(lines)
        )
        try:
            res = server.call_gemini_interactions_api(prompt=prompt, model="gemini-4-hydrogen")
            raw = (res.get("output_text") or "").strip()
            raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.I | re.S).strip()
            items = json.loads(raw)
            updated = 0
            with conn:
                for it in items:
                    bid = it.get("id")
                    tags = [t for t in (it.get("tags") or []) if t in ALLOWED_TAGS][:2]
                    if bid and tags:
                        conn.execute("UPDATE books SET tags = ? WHERE id = ?", (json.dumps(tags), bid))
                        seed_map[bid] = tags
                        updated += 1
            print(f"Batch {i // batch_size + 1}: tagged {updated} books ({res.get('model')})", flush=True)
        except Exception as e:
            print(f"Batch {i // batch_size + 1} error: {e}", flush=True)

    conn.close()
    SEED_PATH.write_text(json.dumps(seed_map, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Saved {len(seed_map)} entries to {SEED_PATH.name}", flush=True)


if __name__ == "__main__":
    run_autotag()
