"""PDF parsing (PyMuPDF), folder-level chapter filtering, and BookTool compilation integration."""

from __future__ import annotations

import json
import os
import re
import shutil
import statistics
import sys
import tempfile
from collections import Counter
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


PDF_TOC_VERSION = 2

_ROMAN_MAP = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}


def _roman_to_int(s: str) -> Optional[int]:
    s = (s or "").strip().lower()
    if not s or not re.match(r"^[ivxlcdm]+$", s):
        return None
    total = 0
    prev = 0
    for ch in reversed(s):
        val = _ROMAN_MAP[ch]
        if val < prev:
            total -= val
        else:
            total += val
            prev = val
    return total if 1 <= total <= 200 else None


def _clean_line(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("\u00a0", " ")).strip()


def _estimate_folio_offsets(doc: fitz.Document) -> Tuple[Optional[int], Optional[int]]:
    """
    Estimates (arabic_offset, roman_offset) where:
      pdf_page_1based = printed_page + offset
    by sampling header/footer page folios across the document.
    """
    page_count = len(doc)
    arabic_offsets: Counter = Counter()
    roman_offsets: Counter = Counter()

    # Sample roman folios in first 35 pages
    for idx in range(min(page_count, 35)):
        page = doc[idx]
        h = page.rect.height
        for b in page.get_text("dict").get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                _, y0, _, y1 = ln["bbox"]
                if not (y1 < h * 0.14 or y0 > h * 0.86):
                    continue
                txt = _clean_line("".join(s.get("text", "") for s in ln.get("spans", [])))
                m_rom = re.match(r"^([ivxlcdm]{1,6})$", txt, re.IGNORECASE)
                if m_rom:
                    rval = _roman_to_int(m_rom.group(1))
                    if rval and 1 <= rval <= 60:
                        off = (idx + 1) - rval
                        if -5 <= off <= 25:
                            roman_offsets[off] += 1

    # Sample arabic folios across pages 5..min(page_count, 90)
    start_i = min(5, page_count // 6)
    end_i = min(page_count, start_i + 80)
    for idx in range(start_i, end_i):
        page = doc[idx]
        h = page.rect.height
        for b in page.get_text("dict").get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                _, y0, _, y1 = ln["bbox"]
                if not (y1 < h * 0.14 or y0 > h * 0.86):
                    continue
                txt = _clean_line("".join(s.get("text", "") for s in ln.get("spans", [])))
                m = (
                    re.match(r"^(\d{1,4})$", txt)
                    or re.search(r"(?:^|\s)(\d{1,4})$", txt)
                    or re.match(r"^(\d{1,4})\s+", txt)
                )
                if m:
                    printed = int(m.group(1))
                    if 1 <= printed <= page_count:
                        off = (idx + 1) - printed
                        if -20 <= off <= 80:
                            arabic_offsets[off] += 1

    best_arabic = None
    if arabic_offsets:
        val, cnt = arabic_offsets.most_common(1)[0]
        if cnt >= 3:
            best_arabic = val

    best_roman = None
    if roman_offsets:
        val, cnt = roman_offsets.most_common(1)[0]
        if cnt >= 2:
            best_roman = val

    return best_arabic, best_roman


def _format_toc_title(raw: str) -> str:
    t = _clean_line(raw).strip(" .·•-–—:")
    if not t:
        return ""
    # Fix OCR/PDF hyphenation artifacts like "Acknowl edgments" -> keep normal spaces, collapse stray internal splits
    letters = [c for c in t if c.isalpha()]
    all_caps = bool(letters and sum(1 for c in letters if c.isupper()) / len(letters) > 0.85 and len(t) > 4)
    # Detect PDF small-caps encoding glitches like "StapLe beCOmingS", "intrOduCtiOn", "COnCLuSiOn"
    weird_smallcaps = bool(re.search(r"[a-z][A-Z]{1,}[a-z]|[a-z][A-Z]$|^[a-z].*[A-Z]", t))
    if all_caps or weird_smallcaps:
        small = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
        words = t.lower().split()
        out = []
        for wi, w in enumerate(words):
            if wi > 0 and w in small:
                out.append(w)
            elif w.startswith("(") and len(w) > 1:
                out.append("(" + w[1:2].upper() + w[2:])
            else:
                out.append(w[:1].upper() + w[1:])
        t = " ".join(out)
    return t


def _detect_toc_from_printed_contents(doc: fitz.Document) -> List[Tuple[int, str, int]]:
    """
    Tier 2: Parses a printed Table of Contents in the first ~25 pages and maps
    printed page numbers (arabic & roman) to 1-based PDF page numbers.
    Returns list of (level, title, pdf_page_1based).
    """
    page_count = len(doc)
    if page_count < 10:
        return []

    scan_limit = min(page_count, 25)
    toc_pages: List[int] = []
    for i in range(scan_limit):
        txt = doc[i].get_text("text") or ""
        lines = [_clean_line(l) for l in txt.splitlines() if l.strip()]
        if any(re.match(r"^(?:table\s+of\s+)?contents$", l, re.IGNORECASE) for l in lines[:8]):
            toc_pages.append(i)
            for nxt in range(i + 1, min(i + 5, scan_limit)):
                nxt_lines = [_clean_line(l) for l in (doc[nxt].get_text("text") or "").splitlines() if l.strip()]
                num_endings = sum(
                    1 for l in nxt_lines if re.search(r"(?:\s|\.{2,})(?:\d{1,4}|[ivxlcdm]{1,6})$", l, re.IGNORECASE)
                )
                if num_endings >= 3:
                    toc_pages.append(nxt)
                else:
                    break
            break

    if not toc_pages:
        return []

    arabic_off, roman_off = _estimate_folio_offsets(doc)
    if arabic_off is None:
        return []

    # Reconstruct visual lines from text dict (preserving column order on each y-band)
    raw_lines: List[str] = []
    for p_idx in toc_pages:
        blocks = doc[p_idx].get_text("dict").get("blocks", [])
        y_rows: List[Tuple[float, float, str]] = []
        for b in blocks:
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                x0, y0, x1, y1 = ln["bbox"]
                txt = _clean_line("".join(s.get("text", "") for s in ln.get("spans", [])))
                if txt:
                    y_rows.append((y0, x0, txt))
        y_rows.sort(key=lambda r: (round(r[0] / 4.0), r[1]))

        # Group spans on the same visual horizontal line (within ~4pt)
        grouped: List[List[Tuple[float, str]]] = []
        last_y: Optional[float] = None
        for y0, x0, txt in y_rows:
            if last_y is None or abs(y0 - last_y) > 4.5:
                grouped.append([(x0, txt)])
                last_y = y0
            else:
                grouped[-1].append((x0, txt))

        for row in grouped:
            row.sort(key=lambda r: r[0])
            # Join with double-space when there's a noticeable x-gap so multi-entry lines stay splittable
            joined = "  ".join(t for _, t in row)
            raw_lines.append(joined)

    # Parse entries (including multi-entry lines like "Notes 239  References 271  Index 289")
    multi_back_re = re.compile(
        r"\b(Notes|Endnotes|References|Bibliography|Works Cited|Index|Glossary|Appendix(?:\s+[A-Z0-9]+)?|Acknowledgments?|Preface|Conclusion|Epilogue)\s+(\d{1,4})\b",
        re.IGNORECASE,
    )

    entries: List[Tuple[int, str, int]] = []
    pending_prefix = ""
    toc_end_1based = toc_pages[-1] + 1

    for line in raw_lines:
        clean = _clean_line(line)
        if not clean or re.match(r"^(?:table\s+of\s+)?contents$", clean, re.IGNORECASE):
            continue
        # Skip standalone page numbers (folio of the TOC page itself)
        if re.match(r"^(?:\d{1,4}|[ivxlcdm]{1,6})$", clean, re.IGNORECASE):
            continue

        # Check multi-entry backmatter line (e.g. "Notes 239  References 271  Index 289")
        multi_hits = list(multi_back_re.finditer(clean))
        if len(multi_hits) >= 2:
            pending_prefix = ""
            for mh in multi_hits:
                title = _format_toc_title(mh.group(1))
                pg_pdf = int(mh.group(2)) + arabic_off
                if toc_end_1based < pg_pdf <= page_count:
                    entries.append((1, title, pg_pdf))
            continue

        # Standard line ending in arabic page number: "1  STAPLE BECOMINGS  39"
        m_ar = re.match(r"^(.+?)(?:\s*\.{2,}\s*|\s+)(\d{1,4})$", clean)
        if m_ar:
            raw_title = (f"{pending_prefix} {m_ar.group(1)}" if pending_prefix else m_ar.group(1)).strip()
            pending_prefix = ""
            printed_pg = int(m_ar.group(2))
            pdf_pg = printed_pg + arabic_off
            title = _format_toc_title(raw_title)
            # Format leading chapter number cleanly: "1 Staple Becomings" -> "1. Staple Becomings"
            m_chnum = re.match(r"^(\d{1,2})\s+([A-Za-z].+)$", title)
            if m_chnum:
                title = f"{m_chnum.group(1)}. {m_chnum.group(2)}"
            if len(title) >= 3 and toc_end_1based <= pdf_pg <= page_count:
                entries.append((1, title, pdf_pg))
            continue

        # Frontmatter line ending in roman numeral: "Preface ix", "Acknowledgments xvii"
        m_rom = re.match(r"^(.+?)(?:\s*\.{2,}\s*|\s+)([ivxlcdm]{1,6})$", clean, re.IGNORECASE)
        if m_rom:
            rval = _roman_to_int(m_rom.group(2))
            if rval:
                raw_title = (f"{pending_prefix} {m_rom.group(1)}" if pending_prefix else m_rom.group(1)).strip()
                pending_prefix = ""
                title = _format_toc_title(raw_title)
                # Use calibrated roman offset, or fall back to matching the title on front pages (1..arabic_off)
                pdf_pg: Optional[int] = None
                if roman_off is not None:
                    cand = rval + roman_off
                    if 1 <= cand <= page_count:
                        pdf_pg = cand
                else:
                    # Search frontmatter pages (after TOC page, before printed page 1) for this heading
                    search_end = min(page_count, max(toc_end_1based + 2, arabic_off + 1))
                    first_word = re.sub(r"[^a-z]", "", (title.split()[0] if title else "").lower())
                    if len(first_word) >= 4:
                        for fp_idx in range(toc_end_1based, search_end):
                            head_txt = (doc[fp_idx].get_text("text") or "")[:300].lower()
                            if first_word in head_txt:
                                pdf_pg = fp_idx + 1
                                # Calibrate roman_off from this match for remaining roman entries!
                                roman_off = pdf_pg - rval
                                break
                if pdf_pg is not None and len(title) >= 3:
                    entries.append((1, title, pdf_pg))
                continue

        # Otherwise might be a wrapped title or "Chapter 1" line preceding the next line
        if len(clean) <= 90 and not re.match(r"^(?:part\s+[ivx\d]+)$", clean, re.IGNORECASE):
            pending_prefix = f"{pending_prefix} {clean}".strip() if pending_prefix else clean
        else:
            pending_prefix = ""

    # Keep monotonically increasing entries by page number
    mono: List[Tuple[int, str, int]] = []
    last_pg = 0
    for lvl, title, pg in entries:
        if pg > last_pg:
            mono.append((lvl, title, pg))
            last_pg = pg

    return mono if len(mono) >= 3 else []


def _detect_toc_from_headings(doc: fitz.Document) -> List[Tuple[int, str, int]]:
    """
    Tier 3: Visual chapter-opener heading scan when a PDF has neither embedded bookmarks
    nor a parseable printed Contents page.
    """
    page_count = len(doc)
    if page_count < 20:
        return []

    # Estimate typical body font size from a sample of interior pages
    size_counts: Counter = Counter()
    sample_start = min(15, page_count // 5)
    for idx in range(sample_start, min(page_count, sample_start + 30)):
        for b in doc[idx].get_text("dict").get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    t = (sp.get("text") or "").strip()
                    if len(t) >= 4:
                        size_counts[round(float(sp.get("size") or 10.0), 1)] += len(t)
    body_sz = size_counts.most_common(1)[0][0] if size_counts else 10.5

    heading_re = re.compile(
        r"^(?:chapter\s+(?:\d+|[ivxlcdm]+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\b|"
        r"preface\b|foreword\b|prologue\b|introduction\b|conclusion\b|epilogue\b|afterword\b|"
        r"notes\b|endnotes\b|bibliography\b|references\b|works\s+cited\b|index\b)",
        re.IGNORECASE,
    )

    entries: List[Tuple[int, str, int]] = []
    for idx in range(page_count):
        page = doc[idx]
        h = page.rect.height
        top_lines: List[Tuple[float, float, str]] = []
        for b in page.get_text("dict").get("blocks", []):
            if b.get("type") != 0:
                continue
            for ln in b.get("lines", []):
                _, y0, _, _ = ln["bbox"]
                if y0 > h * 0.42:
                    continue
                spans = [s for s in ln.get("spans", []) if (s.get("text") or "").strip()]
                if not spans:
                    continue
                txt = _clean_line("".join(s.get("text", "") for s in spans))
                if not txt:
                    continue
                max_sz = max(float(s.get("size") or 0.0) for s in spans)
                top_lines.append((y0, max_sz, txt))

        top_lines.sort(key=lambda r: r[0])
        for li, (_, sz, txt) in enumerate(top_lines):
            if sz >= body_sz * 1.25 and heading_re.match(txt) and len(txt) < 110:
                title = _format_toc_title(txt)
                if li + 1 < len(top_lines) and top_lines[li + 1][1] >= body_sz * 1.15:
                    nxt = _format_toc_title(top_lines[li + 1][2])
                    if nxt and len(nxt) < 90 and not heading_re.match(nxt):
                        title = f"{title}: {nxt}"
                entries.append((1, title, idx + 1))
                break

    return entries if len(entries) >= 3 else []


def parse_pdf_file(pdf_path: Path, fallback_title: str = "", fallback_author: str = "") -> Tuple[dict, List[Tuple[int, str, str]], Optional[bytes]]:
    """
    Parses a PDF into a Folio-compatible manifest, chapter plain-text tuples for FTS5,
    and an optional JPEG cover thumbnail of page 1.
    Uses a 3-tier TOC strategy:
      1. Embedded PDF bookmarks (doc.get_toc)
      2. Printed Table of Contents + automatic folio offset calibration
      3. Visual chapter-opener headings
    """
    with fitz.open(str(pdf_path)) as doc:
        page_count = len(doc)
        meta = doc.metadata or {}
        raw_title = (meta.get("title") or "").strip()
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

        # Tier 1: Embedded PDF bookmarks
        raw_toc = doc.get_toc(simple=True) or []
        valid_toc: List[Tuple[int, str, int]] = [
            (int(lvl), _clean_line(str(t or "")), max(1, min(page_count, int(pg))))
            for lvl, t, pg in raw_toc
            if str(t or "").strip() and isinstance(pg, (int, float)) and int(pg) >= 1
        ]
        toc_source = "bookmarks"

        # Tier 2: Printed Table of Contents + folio offset
        if len(valid_toc) < 2 and page_count >= 10:
            printed_toc = _detect_toc_from_printed_contents(doc)
            if printed_toc:
                valid_toc = printed_toc
                toc_source = "printed_toc"

        # Tier 3: Visual chapter-opener headings
        if len(valid_toc) < 2 and page_count >= 20:
            heading_toc = _detect_toc_from_headings(doc)
            if heading_toc:
                valid_toc = heading_toc
                toc_source = "headings"

        # Build chapter page ranges from TOC entries if available
        chapters: List[dict] = []
        if len(valid_toc) >= 2 and page_count > 0:
            min_lvl = min(lvl for lvl, _, _ in valid_toc)
            lvl1 = [(t, pg) for lvl, t, pg in valid_toc if lvl == min_lvl]
            lvl2 = [(t, pg) for lvl, t, pg in valid_toc if lvl == min_lvl + 1]
            # If level 1 is mostly Part wrappers (Part I, Part II) and level 2 has the real chapters, include level 2
            part_cnt = sum(1 for t, _ in lvl1 if re.match(r"^part\s+[ivx\d]+\b", t, re.IGNORECASE))
            if part_cnt >= 2 and len(lvl2) >= 3:
                top_entries = [
                    (t, pg)
                    for lvl, t, pg in valid_toc
                    if lvl == min_lvl + 1 or (lvl == min_lvl and not re.match(r"^part\s+[ivx\d]+\b", t, re.IGNORECASE))
                ]
            else:
                top_entries = lvl1 if len(lvl1) >= 2 else [(t, pg) for _, t, pg in valid_toc]

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
            toc_source = "page_chunks"
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
        "toc_version": PDF_TOC_VERSION,
        "toc_source": toc_source,
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
