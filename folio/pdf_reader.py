"""PDF parsing (PyMuPDF), folder-level chapter filtering, and BookTool compilation integration."""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import fitz  # PyMuPDF

from .config import (
    BOOKS_DIR,
    BOOKTOOL_DIR,
    CAMBRIDGE_CORE_BOOKS_FOLDER_ID,
    CAMBRIDGE_CORE_ELEMENTS_FOLDER_ID,
    CAMBRIDGE_CORE_JOURNALS_FOLDER_ID,
)

fitz.TOOLS.mupdf_display_errors(False)

# Filename patterns for chapter/frontmatter/backmatter PDFs
_FRONT_TOC_RE = re.compile(
    r"(?:^|[\s_\-.])(?:contents|table[\s_]+of[\s_]+contents|frontmatter|front[\s_]+matter|front|title[\s_]+page|copyright|half[\s_]+title)\s*\.pdf$",
    re.IGNORECASE,
)
_BACKMATTER_RE = re.compile(
    r"(?:^|[\s_\-.])(?:index|bibliography|biblio|references|notes|endnotes|acknowledg(?:e)?ments|preface|contributors|list[\s_]+of[\s_]+(?:figures|tables|illustrations)|about[\s_]+the[\s_]+author|backmatter|back[\s_]+matter|colophon|appendix)\s*\.pdf$",
    re.IGNORECASE,
)
_DEGRUYTER_BRILL_RE = re.compile(
    r"^\d+(?:\.\d+)?_pp_[ivxlcdm0-9]+_[ivxlcdm0-9]+_.*\.pdf$",
    re.IGNORECASE,
)
_CHAPTER_NUM_RE = re.compile(
    r"(?:^|[\s_\-.])(?:ch(?:ap(?:ter)?)?\.?\s*\d+|part[\s_]+[ivx\d]+|intro(?:duction)?|conclusion|epilogue|prologue)\s*(?:[\s_\-.]|\.(?=pdf$))",
    re.IGNORECASE,
)
_COMPLETE_BOOK_RE = re.compile(
    r"(?:-\s*complete|_compiled|\bcomplete\s+book|\bfull\s+book)\s*\.pdf$",
    re.IGNORECASE,
)
_ARTICLE_NAME_RE = re.compile(
    r"(?:^[a-z]+-(?:[a-z]+-|et-al-)?(?:19|20)\d{2}-| - (?:19|20)\d{2} - |^\d{4}\s+-\s+|journal|review|quarterly)",
    re.IGNORECASE,
)


def _norm_stem(name: str) -> str:
    s = re.sub(r"\.pdf$", "", (name or "").lower())
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return s


def is_chapter_like_filename(name: str) -> bool:
    """Returns True if an individual PDF filename looks like a book chapter or front/backmatter."""
    n = (name or "").strip()
    if not n.lower().endswith(".pdf"):
        return False
    if _COMPLETE_BOOK_RE.search(n) or " -- " in n:
        return False
    return bool(
        _FRONT_TOC_RE.search(n)
        or _BACKMATTER_RE.search(n)
        or _DEGRUYTER_BRILL_RE.match(n)
        or _CHAPTER_NUM_RE.search(n)
    )


def _pick_full_book_candidate(folder_name: str, pdfs: List[dict]) -> Optional[dict]:
    """
    Given a list of PDF dicts in a book subfolder, returns the full-book PDF if one exists,
    otherwise None.
    """
    if not pdfs:
        return None

    # 1. Explicit "- Complete.pdf" / "_compiled.pdf"
    complete_hits = [f for f in pdfs if _COMPLETE_BOOK_RE.search(f.get("name") or "")]
    if complete_hits:
        return max(complete_hits, key=lambda f: int(f.get("size") or 0))

    # 2. User's "author -- book title.pdf" convention
    dash_hits = [f for f in pdfs if " -- " in (f.get("name") or "")]
    if dash_hits:
        return max(dash_hits, key=lambda f: int(f.get("size") or 0))

    # 3. Filename matches folder name (e.g. BookTool output "<Folder Name>.pdf")
    #    Must NOT be a tiny front/intro file (e.g. "After Marx.pdf" 84 KB among 120 KB chapters).
    norm_folder = _norm_stem(folder_name)
    sizes = [int(f.get("size") or 0) for f in pdfs if int(f.get("size") or 0) > 0]
    median_size = statistics.median(sizes) if sizes else 0

    for f in sorted(pdfs, key=lambda x: int(x.get("size") or 0), reverse=True):
        fname = f.get("name") or ""
        if is_chapter_like_filename(fname):
            continue
        fsize = int(f.get("size") or 0)
        fstem = _norm_stem(fname)
        name_matches = bool(
            norm_folder
            and (
                fstem == norm_folder
                or fstem == f"{norm_folder} compiled"
                or (len(norm_folder) >= 8 and fstem.startswith(norm_folder))
            )
        )
        if name_matches and (len(pdfs) == 1 or (fsize >= 400_000 and fsize >= median_size * 2.2)):
            return f

    return None


def classify_pdf_folder(
    folder_id: str,
    folder_name: str,
    folder_path: str,
    pdfs: List[dict],
    ancestor_ids: set,
) -> Tuple[str, List[dict]]:
    """
    Classifies a Drive folder's PDFs.
    Returns (status, kept_pdfs) where status is one of:
      - 'cambridge_book_complete'
      - 'cambridge_book_uncompiled'
      - 'cambridge_element'
      - 'cambridge_journal'
      - 'chapter_book_complete'
      - 'chapter_book_uncompiled'
      - 'regular'
    Each kept PDF dict has 'pdf_category' set ('book' | 'element' | 'article').
    """
    if not pdfs:
        return "regular", []

    # 1. Cambridge Core / Elements
    if folder_id == CAMBRIDGE_CORE_ELEMENTS_FOLDER_ID or CAMBRIDGE_CORE_ELEMENTS_FOLDER_ID in ancestor_ids:
        out = []
        for f in pdfs:
            if not _FRONT_TOC_RE.search(f.get("name") or ""):
                f["pdf_category"] = "element"
                out.append(f)
        return "cambridge_element", out

    # 2. Cambridge Core / Journals
    if folder_id == CAMBRIDGE_CORE_JOURNALS_FOLDER_ID or CAMBRIDGE_CORE_JOURNALS_FOLDER_ID in ancestor_ids:
        out = []
        for f in pdfs:
            f["pdf_category"] = "article"
            out.append(f)
        return "cambridge_journal", out

    # 3. Cambridge Core / Books / <Book Subfolder>
    if CAMBRIDGE_CORE_BOOKS_FOLDER_ID in ancestor_ids and folder_id != CAMBRIDGE_CORE_BOOKS_FOLDER_ID:
        full_book = _pick_full_book_candidate(folder_name, pdfs)
        if full_book:
            full_book["pdf_category"] = "book"
            return "cambridge_book_complete", [full_book]
        return "cambridge_book_uncompiled", []

    # 4. General / nerd stuff folder: detect whether this folder is a chapter-split book
    front_toc_count = sum(1 for f in pdfs if _FRONT_TOC_RE.search(f.get("name") or ""))
    degruyter_count = sum(1 for f in pdfs if _DEGRUYTER_BRILL_RE.match(f.get("name") or ""))
    chapter_num_count = sum(1 for f in pdfs if _CHAPTER_NUM_RE.search(f.get("name") or ""))
    backmatter_count = sum(1 for f in pdfs if _BACKMATTER_RE.search(f.get("name") or ""))

    is_chapter_folder = (
        len(pdfs) >= 3
        and (
            front_toc_count >= 1
            or degruyter_count >= 2
            or (chapter_num_count >= 2 and (front_toc_count + backmatter_count + chapter_num_count) >= 3)
        )
    )

    if is_chapter_folder:
        full_book = _pick_full_book_candidate(folder_name, pdfs)
        if full_book:
            full_book["pdf_category"] = "book"
            return "chapter_book_complete", [full_book]
        return "chapter_book_uncompiled", []

    # 5. Regular folder (books + journal articles + essays)
    out = []
    for f in pdfs:
        fname = f.get("name") or ""
        # Skip stray loose frontmatter/contents/De Gruyter single-section files
        if _FRONT_TOC_RE.search(fname) or _DEGRUYTER_BRILL_RE.match(fname):
            continue
        fsize = int(f.get("size") or 0)
        if _ARTICLE_NAME_RE.search(fname) or (fsize > 0 and fsize < 750_000 and " -- " not in fname):
            f["pdf_category"] = "article"
        else:
            f["pdf_category"] = "book"
        out.append(f)
    return "regular", out


def clean_pdf_display_title(filename: str, folder_name: str = "") -> Tuple[str, str]:
    """
    Extracts a clean (title, author) from common PDF naming patterns:
    - "Author -- Book Title.pdf"
    - "Book Title - Complete.pdf"
    - "History and Theory - 2026 - Author - Title.pdf"
    """
    stem = re.sub(r"\.pdf$", "", (filename or "").strip(), flags=re.IGNORECASE)
    stem = re.sub(r"\s*-\s*complete$", "", stem, flags=re.IGNORECASE)
    stem = re.sub(r"_compiled$", "", stem, flags=re.IGNORECASE)

    if " -- " in stem:
        parts = stem.split(" -- ", 1)
        author = parts[0].strip()
        title = parts[1].strip()
        return title or stem, author

    # Academic article pattern: "Journal - 2026 - Author - Title"
    m_art = re.match(r"^.+?\s-\s(?:19|20)\d{2}\s-\s([^-]+?)\s-\s(.+)$", stem)
    if m_art:
        return m_art.group(2).strip(), m_art.group(1).strip()

    clean = stem.replace("_", " ").strip()
    # Title-case only if entirely lowercase
    if clean == clean.lower():
        clean = " ".join(w[:1].upper() + w[1:] for w in clean.split())
    return clean or folder_name or filename, ""


def render_pdf_cover(pdf_path: Path, max_width: int = 420) -> Optional[bytes]:
    """Renders page 1 of a PDF to a JPEG thumbnail."""
    try:
        with fitz.open(str(pdf_path)) as doc:
            if len(doc) == 0:
                return None
            page = doc[0]
            rect = page.rect
            scale = min(1.5, max_width / max(rect.width, 1.0))
            pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
            return pix.tobytes("jpeg", jpg_quality=82)
    except Exception:
        return None


def parse_pdf_file(pdf_path: Path, fallback_title: str = "", fallback_author: str = "") -> Tuple[dict, List[Tuple[int, str, str]], Optional[bytes]]:
    """
    Parses a PDF into a Folio-compatible manifest, chapter plain-text tuples for FTS5,
    and an optional JPEG cover thumbnail of page 1.
    """
    with fitz.open(str(pdf_path)) as doc:
        page_count = len(doc)
        meta = doc.metadata or {}
        raw_title = (meta.get("title") or "").strip()
        # Ignore unhelpful PDF metadata titles like "Microsoft Word - ..." or bare filenames
        if (
            not raw_title
            or len(raw_title) < 3
            or raw_title.lower().endswith(".pdf")
            or raw_title.lower().startswith(("microsoft word", "untitled", "adobe", "cambridge", "doi:"))
        ):
            title = fallback_title or pdf_path.stem
        else:
            title = raw_title
        author = (meta.get("author") or "").strip() or fallback_author
        publisher = (meta.get("creator") or meta.get("producer") or "").strip()

        raw_toc = doc.get_toc(simple=True) or []
        # Filter valid 1-based page numbers
        valid_toc = [
            (int(lvl), str(t or "").strip(), max(1, min(page_count, int(pg))))
            for lvl, t, pg in raw_toc
            if str(t or "").strip() and isinstance(pg, (int, float)) and int(pg) >= 1
        ]

        # Build chapter page ranges from top-level TOC entries if available
        chapters: List[dict] = []
        if len(valid_toc) >= 2 and page_count > 0:
            min_lvl = min(lvl for lvl, _, _ in valid_toc)
            top_entries = [(t, pg) for lvl, t, pg in valid_toc if lvl == min_lvl]
            if len(top_entries) < 2:
                top_entries = [(t, pg) for _, t, pg in valid_toc]

            # Deduplicate entries starting on the same page
            deduped: List[Tuple[str, int]] = []
            seen_pages = set()
            for t, pg in top_entries:
                if pg not in seen_pages:
                    seen_pages.add(pg)
                    deduped.append((t, pg))
            deduped.sort(key=lambda x: x[1])

            if deduped and deduped[0][1] > 1:
                deduped.insert(0, ("Front Matter", 1))

            for idx, (ch_title, start_pg) in enumerate(deduped):
                if idx + 1 < len(deduped):
                    end_pg = max(start_pg, deduped[idx + 1][1] - 1)
                else:
                    end_pg = page_count
                chapters.append(
                    {
                        "index": idx,
                        "href": f"page:{start_pg}",
                        "title": ch_title,
                        "start_page": start_pg,
                        "end_page": end_pg,
                        "word_count": 0,
                    }
                )
        elif page_count > 0:
            if page_count <= 35:
                chapters.append(
                    {
                        "index": 0,
                        "href": "page:1",
                        "title": f"Full Text (pp. 1–{page_count})",
                        "start_page": 1,
                        "end_page": page_count,
                        "word_count": 0,
                    }
                )
            else:
                chunk_size = 20
                idx = 0
                for start_pg in range(1, page_count + 1, chunk_size):
                    end_pg = min(page_count, start_pg + chunk_size - 1)
                    chapters.append(
                        {
                            "index": idx,
                            "href": f"page:{start_pg}",
                            "title": f"Pages {start_pg}–{end_pg}",
                            "start_page": start_pg,
                            "end_page": end_pg,
                            "word_count": 0,
                        }
                    )
                    idx += 1

        # Extract plain text per chapter for FTS5 and word counts
        chapter_plain_texts: List[Tuple[int, str, str]] = []
        for ch in chapters:
            parts: List[str] = []
            for pno in range(ch["start_page"] - 1, min(page_count, ch["end_page"])):
                try:
                    txt = doc[pno].get_text("text") or ""
                    if txt.strip():
                        parts.append(f"[p. {pno + 1}]\n{txt.strip()}")
                except Exception:
                    pass
            joined = "\n\n".join(parts)
            ch["word_count"] = len(joined.split())
            chapter_plain_texts.append((ch["index"], ch["title"], joined))

        # Build TOC list mapped to chapter_index
        def _chapter_for_page(pg: int) -> int:
            for ch in chapters:
                if ch["start_page"] <= pg <= ch["end_page"]:
                    return ch["index"]
            return 0

        toc_out: List[dict] = []
        if valid_toc:
            min_lvl = min(lvl for lvl, _, _ in valid_toc)
            for lvl, t, pg in valid_toc:
                toc_out.append(
                    {
                        "title": t,
                        "href": f"page:{pg}",
                        "page": pg,
                        "level": max(0, lvl - min_lvl),
                        "chapter_index": _chapter_for_page(pg),
                    }
                )
        else:
            for ch in chapters:
                toc_out.append(
                    {
                        "title": ch["title"],
                        "href": ch["href"],
                        "page": ch["start_page"],
                        "level": 0,
                        "chapter_index": ch["index"],
                    }
                )

        # Render cover thumbnail of page 1
        cover_bytes: Optional[bytes] = None
        if page_count > 0:
            try:
                page0 = doc[0]
                scale = min(1.5, 420.0 / max(page0.rect.width, 1.0))
                pix = page0.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                cover_bytes = pix.tobytes("jpeg", jpg_quality=82)
            except Exception:
                cover_bytes = None

    manifest = {
        "format": "pdf",
        "title": title,
        "author": author,
        "publisher": publisher,
        "cover_href": "cover.jpg" if cover_bytes else "",
        "opf_dir": "",
        "page_count": page_count,
        "chapters": chapters,
        "toc": toc_out,
    }
    return manifest, chapter_plain_texts, cover_bytes


def get_pdf_chapter_plain_text(book_id: str, chapter: dict) -> Tuple[str, str]:
    """Extracts plain text for a PDF chapter's page range."""
    pdf_path = BOOKS_DIR / book_id / "book.pdf"
    title = chapter.get("title") or "PDF Section"
    if not pdf_path.is_file():
        return title, ""
    start_pg = max(1, int(chapter.get("start_page") or 1))
    end_pg = max(start_pg, int(chapter.get("end_page") or start_pg))
    parts: List[str] = []
    try:
        with fitz.open(str(pdf_path)) as doc:
            for pno in range(start_pg - 1, min(len(doc), end_pg)):
                txt = (doc[pno].get_text("text") or "").strip()
                if txt:
                    parts.append(f"[p. {pno + 1}]\n{txt}")
    except Exception:
        return title, ""
    return title, "\n\n".join(parts)


def _natural_chapter_sort_key(filename: str) -> Tuple[int, float, str]:
    """Fallback chapter sorter when sibling BookTool is unavailable (e.g. on Railway)."""
    low = filename.lower()
    m_dg = re.match(r"^(\d+(?:\.\d+)?)_pp_", low)
    if m_dg:
        return (1, float(m_dg.group(1)), low)
    if re.search(r"\b(frontmatter|front|half[\s_]+title|title[\s_]+page)\b", low):
        return (0, 0.0, low)
    if re.search(r"\b(contents|toc)\b", low):
        return (0, 1.0, low)
    if re.search(r"\b(preface|foreword|acknowledg)\b", low):
        return (0, 2.0, low)
    if re.search(r"\b(intro|introduction|prologue)\b", low):
        return (0, 3.0, low)
    m_ch = re.search(r"\bch(?:ap(?:ter)?)?\.?\s*(\d+)\b", low)
    if m_ch:
        return (1, float(m_ch.group(1)), low)
    if re.search(r"\b(conclusion|epilogue|afterword)\b", low):
        return (2, 0.0, low)
    if re.search(r"\b(appendix)\b", low):
        return (2, 1.0, low)
    if re.search(r"\b(notes|endnotes)\b", low):
        return (2, 2.0, low)
    if re.search(r"\b(biblio|bibliography|references)\b", low):
        return (2, 3.0, low)
    if re.search(r"\b(index)\b", low):
        return (2, 4.0, low)
    return (1, 500.0, low)


def compile_local_chapters_with_booktool(
    chapter_dir: Path,
    output_dir: Path,
    book_name: str,
) -> Tuple[Path, dict]:
    """
    Compiles a local directory of chapter PDFs into a single full-book PDF with TOC bookmarks.
    Uses the user's BookTool (`booktool.pdf_ops.compile_chapters_to_book_pdf`) when available,
    falling back to a built-in PyMuPDF compiler if BookTool is not on disk (e.g., Railway).
    """
    # Try sibling BookTool first
    if BOOKTOOL_DIR.is_dir():
        parent_str = str(BOOKTOOL_DIR.parent)
        if parent_str not in sys.path:
            sys.path.insert(0, parent_str)
        try:
            from booktool.pdf_ops import compile_chapters_to_book_pdf  # type: ignore

            res = compile_chapters_to_book_pdf(
                inputs=[str(chapter_dir)],
                output_dir=str(output_dir),
                book_name=book_name,
                include_front=True,
                include_toc=True,
                include_backmatter=True,
            )
            return Path(res.output_path), {
                "engine": "booktool",
                "title": res.title,
                "author": res.author,
                "total_pages": res.total_pages,
                "chapter_count": len(res.chapters),
            }
        except Exception as exc:
            print(f"BookTool import/compile failed, falling back to built-in PyMuPDF compiler: {exc}", flush=True)

    # Built-in PyMuPDF fallback compiler (same output convention: "<book_name> - Complete.pdf")
    pdfs = sorted(
        [p for p in chapter_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf"],
        key=lambda p: _natural_chapter_sort_key(p.name),
    )
    if not pdfs:
        raise ValueError("No chapter PDFs found to compile.")

    safe_name = re.sub(r'[\\/:*?"<>|]+', " ", book_name).strip() or "Compiled Book"
    out_path = output_dir / f"{safe_name} - Complete.pdf"
    merged = fitz.open()
    toc_entries: List[list] = []
    cur_page = 1
    for p in pdfs:
        try:
            with fitz.open(str(p)) as src:
                n_pages = len(src)
                if n_pages == 0:
                    continue
                merged.insert_pdf(src)
                sec_title = re.sub(r"\.pdf$", "", p.name, flags=re.IGNORECASE)
                sec_title = re.sub(r"^\d+(?:\.\d+)?_pp_[ivxlcdm0-9]+_[ivxlcdm0-9]+_", "", sec_title, flags=re.IGNORECASE)
                sec_title = sec_title.replace("_", " ").strip()
                toc_entries.append([1, sec_title, cur_page])
                cur_page += n_pages
        except Exception as exc:
            print(f"Skipping unreadable chapter PDF {p.name}: {exc}", flush=True)

    if len(merged) == 0:
        merged.close()
        raise RuntimeError("Failed to merge any pages from chapter PDFs.")

    if toc_entries:
        merged.set_toc(toc_entries)
    merged.set_metadata({"title": safe_name, "creator": "Folio / BookTool"})
    output_dir.mkdir(parents=True, exist_ok=True)
    merged.save(str(out_path), deflate=True, garbage=3)
    total_pages = len(merged)
    merged.close()

    return out_path, {
        "engine": "folio_pymupdf",
        "title": safe_name,
        "author": "",
        "total_pages": total_pages,
        "chapter_count": len(toc_entries),
    }
