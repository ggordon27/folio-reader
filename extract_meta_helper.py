#!/usr/bin/env python3
"""
Extracts official dc:title, subtitle, dc:creator, and dc:publisher from an EPUB file
and updates SQLite books table.
"""
from pathlib import Path
import os
import re
import sqlite3
import sys
import xml.etree.ElementTree as ET
import zipfile

DB_PATH = str(Path(__file__).resolve().parent / "data" / "reader.db")


def strip_ns(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def extract_opf_metadata(epub_path: str):
    with zipfile.ZipFile(epub_path, "r") as zf:
        opf_path = None
        if "META-INF/container.xml" in zf.namelist():
            try:
                tree = ET.fromstring(zf.read("META-INF/container.xml"))
                for el in tree.iter():
                    if strip_ns(el.tag) == "rootfile" and el.attrib.get("full-path"):
                        opf_path = el.attrib["full-path"]
                        break
            except Exception:
                pass
        if not opf_path:
            opfs = [n for n in zf.namelist() if n.endswith(".opf")]
            if opfs:
                opf_path = opfs[0]
        if not opf_path or opf_path not in zf.namelist():
            return "", "", ""

        raw_xml = zf.read(opf_path)
        root = ET.fromstring(raw_xml)

        titles = []
        creators = []
        contributors = []
        publisher = ""

        # Check if there are title-type refinements (main vs subtitle vs edition)
        refinements = {}
        for child in root:
            if strip_ns(child.tag) == "metadata":
                for m in child:
                    if strip_ns(m.tag) == "meta" and m.attrib.get("refines") and m.attrib.get("property") == "title-type":
                        ref_id = m.attrib["refines"].lstrip("#")
                        refinements[ref_id] = (m.text or "").strip().lower()

        main_title = ""
        sub_title = ""

        for child in root:
            if strip_ns(child.tag) == "metadata":
                for m in child:
                    mname = strip_ns(m.tag)
                    val = re.sub(r"\s+", " ", (m.text or "").strip())
                    if not val:
                        continue
                    if mname == "title":
                        tid = m.attrib.get("id", "")
                        ttype = refinements.get(tid, "")
                        if ttype == "main" and not main_title:
                            main_title = val
                        elif ttype in ("sub", "subtitle") and not sub_title:
                            sub_title = val
                        elif ttype in ("edition", "collection", "series"):
                            continue
                        else:
                            titles.append(val)
                    elif mname == "creator":
                        if val not in creators:
                            creators.append(val)
                    elif mname == "contributor":
                        if val not in contributors:
                            contributors.append(val)
                    elif mname == "publisher" and not publisher:
                        publisher = val

        if main_title:
            full_title = f"{main_title}: {sub_title}" if (sub_title and sub_title.lower() not in main_title.lower()) else main_title
        elif len(titles) >= 2 and titles[1].lower() not in titles[0].lower() and not re.search(r"edition", titles[1], re.I):
            full_title = f"{titles[0]}: {titles[1]}"
        elif titles:
            full_title = titles[0]
        else:
            full_title = ""

        author_list = creators if creators else contributors[:2]
        author_str = ", ".join(author_list)
        return full_title, author_str, publisher


def main():
    if len(sys.argv) < 3:
        return
    book_id = sys.argv[1]
    epub_path = sys.argv[2]
    if not os.path.exists(epub_path):
        return
    try:
        title, author, publisher = extract_opf_metadata(epub_path)
        if title or author:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            with conn:
                if title and author:
                    conn.execute(
                        "UPDATE books SET title = ?, author = ?, publisher = COALESCE(NULLIF(?, ''), publisher) WHERE id = ?",
                        (title, author, publisher, book_id),
                    )
                elif title:
                    conn.execute(
                        "UPDATE books SET title = ?, publisher = COALESCE(NULLIF(?, ''), publisher) WHERE id = ?",
                        (title, publisher, book_id),
                    )
                elif author:
                    conn.execute(
                        "UPDATE books SET author = ?, publisher = COALESCE(NULLIF(?, ''), publisher) WHERE id = ?",
                        (author, publisher, book_id),
                    )
            conn.close()
    except Exception as e:
        print(f"Warn {book_id}: {e}", file=sys.stderr)
    finally:
        try:
            os.remove(epub_path)
        except Exception:
            pass


if __name__ == "__main__":
    main()
