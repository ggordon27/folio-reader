#!/usr/bin/env python3
"""
Batch-enriches EPUB titles and authors in SQLite using Gemini API.
Only affects rows where author is currently empty.
"""
from pathlib import Path
import json
import re
import sqlite3
import sys
import time

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))
import server

DB_PATH = str(BASE_DIR / "data" / "reader.db")


def run_batch_enrichment():
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, name, folder_path FROM books WHERE (author = '' OR author IS NULL) ORDER BY modified_time DESC"
    ).fetchall()
    conn.close()

    print(f"Total books needing author/title resolution: {len(rows)}", flush=True)
    batch_size = 75

    for idx in range(0, len(rows), batch_size):
        batch = rows[idx : idx + batch_size]
        lines = []
        for r in batch:
            clean_name = re.sub(r"_for_geoffrey[-_]gordon", "", r["name"], flags=re.I)
            clean_name = re.sub(r"\.epub$", "", clean_name, flags=re.I)
            folder = r["folder_path"] or ""
            lines.append(f'{r["id"]}\t{clean_name}\t{folder}')

        prompt = (
            "You are a scholarly bibliographer. Below is a tab-separated list (`id`, `filename_slug`, `subject_folder`) of published academic and trade books (many from Verso Books, Pluto Press, Routledge, Haymarket, Duke UP, University of California Press, etc.).\n"
            "Note: `_for_geoffrey-gordon` was a watermark suffix and Geoffrey Gordon is NOT the author.\n"
            "For each entry, identify the official published Book Title (in proper Title Case, including subtitle when part of the filename or well-known book title) and the real Author(s) or Editor(s).\n"
            "If a filename starts or ends with an author surname (e.g. `laclau on populist reason`, `butler force of nonviolence`, `althusser lessons on rousseau`, `cleaver 33 lessons on capital`, `ranciere hatred of democracy`), expand the author to their full name (e.g. `Ernesto Laclau`, `Judith Butler`, `Louis Althusser`, `Harry Cleaver`, `Jacques Rancière`) and remove the surname prefix from the book title.\n\n"
            "Input list:\n"
            + "\n".join(lines)
            + "\n\nReturn ONLY a valid JSON array of objects: `[{\"id\": \"...\", \"title\": \"...\", \"author\": \"...\"}, ...]`."
        )

        try:
            res = server.call_gemini_interactions_api(prompt=prompt, model="gemini-3.5-flash-lite")
            raw = res["output_text"].strip()
            # Extract JSON array
            m = re.search(r"\[[\s\S]*\]", raw)
            if not m:
                print(f"Batch {idx}: No JSON array found", flush=True)
                continue
            parsed = json.loads(m.group(0))
            conn = sqlite3.connect(DB_PATH, timeout=30)
            updated = 0
            with conn:
                for item in parsed:
                    bid = item.get("id", "")
                    title = (item.get("title") or "").strip()
                    author = (item.get("author") or "").strip()
                    if author.lower() in ("unknown", "unknown author", "n/a", "geoffrey gordon"):
                        author = ""
                    if bid and title:
                        # Only update if author is still empty (in case OPF extractor already populated exact OPF metadata)
                        conn.execute(
                            """
                            UPDATE books
                            SET title = CASE WHEN author = '' OR author IS NULL THEN ? ELSE title END,
                                author = CASE WHEN author = '' OR author IS NULL THEN ? ELSE author END
                            WHERE id = ?
                            """,
                            (title, author, bid),
                        )
                        updated += 1
            conn.close()
            print(f"Batch {idx // batch_size + 1}: Updated {updated} books", flush=True)
        except Exception as e:
            print(f"Batch {idx // batch_size + 1} error: {e}", flush=True)
            time.sleep(2)


if __name__ == "__main__":
    run_batch_enrichment()
