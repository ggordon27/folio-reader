"""Markdown reading dossier and BibTeX citation formatters."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone

from .config import HIGHLIGHT_SCHEMA
from .db import get_setting


def companion_filename_for(book_name: str, ext: str = ".annotations.json") -> str:
    stem = re.sub(r"\.(?:epub|pdf)$", "", book_name, flags=re.IGNORECASE)
    return f"{stem}{ext}"


def make_citekey(book: dict) -> str:
    title = (book.get("title") or book.get("name") or "Untitled").strip()
    author = (book.get("author") or "Unknown").strip()
    mod_time = (book.get("modified_time") or "")[:4]
    year = mod_time if mod_time.isdigit() else "nd"
    first_author = re.sub(r"[^a-z0-9]", "", (author.split(",")[0].split()[-1] if author else "book").lower())
    first_word = re.sub(r"[^a-z0-9]", "", (title.split()[0] if title else "work").lower())
    return f"{first_author}{year}{first_word}" or "folio_book"


def _yaml_escape(val: str) -> str:
    s = str(val or "").replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"'


def format_annotations_markdown(book: dict, annotations: list) -> str:
    title = (book.get("title") or book.get("name") or "Untitled").strip()
    author = (book.get("author") or "Unknown").strip()
    publisher = (book.get("publisher") or "").strip()
    citekey = make_citekey(book)
    exported_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    raw_tags = book.get("tags") or "[]"
    try:
        tags = json.loads(raw_tags) if isinstance(raw_tags, str) else list(raw_tags)
    except Exception:
        tags = []

    fm = [
        "---",
        f"title: {_yaml_escape(title)}",
        f"author: {_yaml_escape(author)}",
        f"citekey: {citekey}",
    ]
    if publisher:
        fm.append(f"publisher: {_yaml_escape(publisher)}")
    if tags:
        fm.append("tags: [" + ", ".join(_yaml_escape(t) for t in tags) + "]")
    if book.get("web_view_link"):
        fm.append(f"source_url: {_yaml_escape(book['web_view_link'])}")
    fm.extend([f"exported_at: {_yaml_escape(exported_iso)}", "---", ""])

    lines = fm + [
        f"# Reading Notes & Color-Coded Dossier: {title}",
        f"**Author:** {author}  ",
        f"**Exported:** {exported_iso}  ",
        "",
    ]

    saved_report = get_setting(f"report_{book.get('id', '')}", "").strip()
    if saved_report:
        lines.extend(
            [
                "## Synthesized Reading Report",
                "",
                saved_report,
                "",
                "---",
                "",
            ]
        )

    lines.extend(["## Highlights Organized by Analytical Category", ""])

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
            lines.append(f'- **[{ch_title}{pg_str}]** "{item.get("selected_text", "").strip()}"')
            if item.get("note"):
                lines.append(f"  - **Reader Note:** {item['note'].strip()}")
            if item.get("gemini_response"):
                lines.append(f"  - **Gemini Insight:** {item['gemini_response'].strip()}")
        lines.append("")

    lines.extend(["---", "", "## Chronological Highlights by Chapter", ""])

    by_chapter: dict = {}
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
            lines.append(f'> **[{c_key.upper()} — {c_meta["short"]}{pg_badge}]** "{item.get("selected_text", "").strip()}"')
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
