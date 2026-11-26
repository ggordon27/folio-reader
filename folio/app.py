"""FastAPI application for Folio — Google Drive EPUB Reader & Gemini Study Companion."""

from __future__ import annotations

import hmac
import json
import mimetypes
import os
import re
import traceback
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict

import httpx
from fastapi import FastAPI, Query, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse

from .auth import get_active_passcode, is_request_authenticated, make_session_token
from .config import BOOKS_DIR, DATA_DIR, GDRIVE_BIN, HIGHLIGHT_SCHEMA, STATIC_DIR
from .db import (
    compute_progress_summary,
    delete_annotation_fts,
    get_db,
    get_setting,
    init_db,
    resolve_gemini_api_key,
    set_setting,
    upsert_annotation_fts,
)
from .drive import (
    compile_uncompiled_folder_to_drive,
    ensure_book_downloaded_and_parsed,
    export_notes_to_gdoc,
    find_cached_cover,
    get_all_books,
    get_drive_oauth_config,
    get_epub_cache_stats,
    has_drive_oauth,
    hide_book,
    list_hidden_books,
    list_uncompiled_folders,
    prune_stale_epub_cache,
    restore_hidden_book,
    save_cover_thumbnail,
    schedule_book_auto_sync,
    set_drive_token_cache,
    start_background_metadata_enrichment,
    start_periodic_drive_sync,
    sync_annotations_to_drive,
    sync_library_from_drive,
    sync_pdfs_from_drive,
)
from .epub import (
    get_chapter_plain_text,
    read_epub_resource,
    search_all_books_and_notes,
    search_book_passages_fts,
)
from .export import companion_filename_for, format_annotations_markdown, format_bibtex
from .gemini import call_gemini_interactions_api, stream_gemini_interactions_api


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    start_periodic_drive_sync()
    if has_drive_oauth():
        start_background_metadata_enrichment(limit=300)
    print(f"Folio data directory: {DATA_DIR}", flush=True)
    yield


app = FastAPI(title="Folio EPUB Reader", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(GZipMiddleware, minimum_size=512)

_PUBLIC_PATHS = {
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/logout",
    "/api/drive/oauth/callback",
}


@app.middleware("http")
async def auth_and_error_middleware(request: Request, call_next):
    path = request.url.path
    try:
        if (path.startswith("/api/") or path.startswith("/epub/") or path.startswith("/pdf/")) and path not in _PUBLIC_PATHS:
            if not is_request_authenticated(request):
                return JSONResponse(
                    {"error": "Authentication required", "code": "AUTH_REQUIRED"},
                    status_code=401,
                    headers={"Cache-Control": "no-store"},
                )
        return await call_next(request)
    except Exception as exc:
        traceback.print_exc()
        return JSONResponse(
            {"error": str(exc), "code": "ERROR"},
            status_code=500,
            headers={"Cache-Control": "no-store"},
        )


def _base_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme or "http"
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "127.0.0.1:8765"
    return f"{proto}://{host}"


async def _read_json_body(request: Request) -> Dict[str, Any]:
    raw = await request.body()
    if not raw:
        return {}
    return json.loads(raw.decode("utf-8"))


# --- Auth Endpoints ---


@app.get("/api/auth/status")
def api_auth_status(request: Request):
    return JSONResponse(
        {
            "passcode_required": bool(get_active_passcode()),
            "authenticated": is_request_authenticated(request),
        },
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/auth/login")
async def api_auth_login(request: Request):
    body = await _read_json_body(request)
    entered = str(body.get("passcode") or "").strip()
    expected = get_active_passcode()
    if expected and not hmac.compare_digest(entered, expected):
        return JSONResponse({"error": "Incorrect passcode", "code": "INVALID_PASSCODE"}, status_code=401)
    resp = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    if expected:
        tok = make_session_token(expected)
        resp.set_cookie(
            key="folio_session",
            value=tok,
            path="/",
            max_age=30 * 86400,
            httponly=True,
            samesite="lax",
        )
    return resp


@app.post("/api/auth/logout")
def api_auth_logout():
    resp = JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})
    resp.set_cookie(key="folio_session", value="", path="/", max_age=0, httponly=True, samesite="lax")
    return resp


# --- Google Drive OAuth ---


@app.get("/api/drive/oauth/start")
def api_drive_oauth_start(request: Request):
    cfg = get_drive_oauth_config()
    if not (cfg["client_id"] and cfg["client_secret"]):
        return JSONResponse({"error": "Set Google Client ID and Client Secret in Settings first.", "code": "ERROR"}, status_code=400)
    redirect_uri = f"{_base_url(request)}/api/drive/oauth/callback"
    params = {
        "client_id": cfg["client_id"],
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "https://www.googleapis.com/auth/drive",
        "access_type": "offline",
        "prompt": "consent",
    }
    auth_url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)
    return {"auth_url": auth_url}


@app.get("/api/drive/oauth/callback")
def api_drive_oauth_callback(request: Request, code: str = ""):
    code = code.strip()
    if not code:
        return JSONResponse({"error": "Missing OAuth code"}, status_code=400)
    cfg = get_drive_oauth_config()
    redirect_uri = f"{_base_url(request)}/api/drive/oauth/callback"
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": cfg["client_id"],
                "client_secret": cfg["client_secret"],
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
        if resp.status_code != 200:
            return JSONResponse({"error": f"OAuth exchange failed: {resp.text}"}, status_code=400)
        tok_data = resp.json()
    if tok_data.get("refresh_token"):
        set_setting("google_refresh_token", tok_data["refresh_token"])
    set_drive_token_cache(tok_data.get("access_token", ""), float(tok_data.get("expires_in", 3500)))
    return RedirectResponse(url="/?drive_connected=1", status_code=302)


# --- Status, Settings & Cache ---


@app.get("/api/status")
def api_status():
    api_key = resolve_gemini_api_key()
    model = get_setting("gemini_model", "gemini-4-hydrogen")
    oauth_cfg = get_drive_oauth_config()
    return {
        "ok": True,
        "gdrive_connected": has_drive_oauth() or os.path.exists(GDRIVE_BIN),
        "drive_oauth_connected": has_drive_oauth(),
        "google_client_id": oauth_cfg["client_id"],
        "passcode_enabled": bool(get_active_passcode()),
        "gemini_configured": bool(api_key),
        "gemini_key_masked": (api_key[:4] + "..." + api_key[-4:]) if len(api_key) > 8 else ("Configured" if api_key else ""),
        "gemini_model": model,
    }


@app.post("/api/settings")
async def api_settings(request: Request):
    body = await _read_json_body(request)
    if "gemini_api_key" in body:
        set_setting("gemini_api_key", str(body["gemini_api_key"]).strip())
    if "gemini_model" in body and str(body["gemini_model"]).strip():
        set_setting("gemini_model", str(body["gemini_model"]).strip())
    if "google_client_id" in body:
        set_setting("google_client_id", str(body["google_client_id"]).strip())
    if "google_client_secret" in body and str(body["google_client_secret"]).strip():
        set_setting("google_client_secret", str(body["google_client_secret"]).strip())

    new_cookie = None
    if "folio_passcode" in body and not os.environ.get("FOLIO_PASSCODE", "").strip():
        new_pc = str(body["folio_passcode"]).strip()
        set_setting("folio_passcode", new_pc)
        if new_pc:
            new_cookie = make_session_token(new_pc)

    api_key = resolve_gemini_api_key()
    resp = JSONResponse(
        {
            "ok": True,
            "gemini_configured": bool(api_key),
            "gemini_key_masked": (api_key[:4] + "..." + api_key[-4:]) if len(api_key) > 8 else ("Configured" if api_key else ""),
            "gemini_model": get_setting("gemini_model", "gemini-4-hydrogen"),
            "drive_oauth_connected": has_drive_oauth(),
            "passcode_enabled": bool(get_active_passcode()),
        },
        headers={"Cache-Control": "no-store"},
    )
    if new_cookie:
        resp.set_cookie(key="folio_session", value=new_cookie, path="/", max_age=30 * 86400, httponly=True, samesite="lax")
    return resp


@app.get("/api/cache/stats")
def api_cache_stats(days: int = Query(10)):
    return get_epub_cache_stats(days=days)


@app.post("/api/cache/prune")
async def api_cache_prune(request: Request):
    body = await _read_json_body(request)
    days = int(body.get("days", 10))
    return prune_stale_epub_cache(days=days)


# --- Books & Search ---


@app.get("/api/books")
def api_books():
    books = get_all_books()
    if not books:
        books = sync_library_from_drive()
    uncompiled = list_uncompiled_folders()
    return JSONResponse(
        {"books": books, "uncompiled_folders": uncompiled},
        headers={"Cache-Control": "no-store"},
    )


@app.post("/api/books/sync")
def api_books_sync(scope: str = Query("epub")):
    try:
        if scope == "pdf":
            return sync_pdfs_from_drive()
        books = sync_library_from_drive(allow_cache_fallback=False)
        if scope == "all":
            pdf_res = sync_pdfs_from_drive()
            return pdf_res
        return {"books": books, "uncompiled_folders": list_uncompiled_folders()}
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/api/pdfs/uncompiled")
def api_uncompiled_pdfs():
    return JSONResponse({"uncompiled_folders": list_uncompiled_folders()}, headers={"Cache-Control": "no-store"})


@app.post("/api/pdfs/sync")
def api_pdfs_sync():
    try:
        return sync_pdfs_from_drive()
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.post("/api/pdfs/compile/{folder_id}")
def api_compile_uncompiled_folder(folder_id: str):
    try:
        return compile_uncompiled_folder_to_drive(folder_id)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


@app.get("/api/search")
def api_global_search(q: str = Query("")):
    return search_all_books_and_notes(q.strip())


@app.get("/api/books/hidden")
def api_hidden_books():
    return JSONResponse({"hidden_books": list_hidden_books()}, headers={"Cache-Control": "no-store"})


@app.delete("/api/books/{book_id}")
def api_hide_book(book_id: str):
    return hide_book(book_id)


@app.post("/api/books/{book_id}/restore")
def api_restore_book(book_id: str):
    return restore_hidden_book(book_id)


@app.get("/api/books/{book_id}")
def api_get_book(book_id: str):
    book = ensure_book_downloaded_and_parsed(book_id)
    conn = get_db()
    try:
        anns = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM annotations WHERE book_id = ? ORDER BY chapter_index ASC, created_at ASC",
                (book_id,),
            ).fetchall()
        ]
        chats = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM chats WHERE book_id = ? ORDER BY created_at ASC",
                (book_id,),
            ).fetchall()
        ]
        bookmarks = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM bookmarks WHERE book_id = ? ORDER BY chapter_index ASC, scroll_percent ASC, created_at ASC",
                (book_id,),
            ).fetchall()
        ]
    finally:
        conn.close()

    raw_tags = book.get("tags") or "[]"
    try:
        book["tags"] = json.loads(raw_tags) if isinstance(raw_tags, str) else list(raw_tags)
    except Exception:
        book["tags"] = []
    book["annotations"] = anns
    book["chats"] = chats
    book["bookmarks"] = bookmarks
    book["reading_report"] = get_setting(f"report_{book_id}", "")
    return JSONResponse(book, headers={"Cache-Control": "no-store"})


@app.get("/api/books/{book_id}/cover")
def api_book_cover(book_id: str):
    cached_path = find_cached_cover(book_id)
    if cached_path:
        ctype, _ = mimetypes.guess_type(str(cached_path))
        return Response(
            content=cached_path.read_bytes(),
            media_type=ctype or "image/jpeg",
            headers={"Cache-Control": "private, max-age=604800"},
        )
    conn = get_db()
    try:
        row = conn.execute("SELECT cover_href, downloaded, COALESCE(format, 'epub') AS format FROM books WHERE id = ?", (book_id,)).fetchone()
    finally:
        conn.close()
    if row and row["downloaded"]:
        if row["format"] == "pdf":
            pdf_path = BOOKS_DIR / book_id / "book.pdf"
            if pdf_path.is_file():
                from .pdf_reader import render_pdf_cover

                raw = render_pdf_cover(pdf_path)
                if raw:
                    save_cover_thumbnail(book_id, raw, "cover.jpg")
                    return Response(
                        content=raw,
                        media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=604800"},
                    )
        elif row["cover_href"]:
            res = read_epub_resource(book_id, row["cover_href"])
            if res:
                raw, ctype = res
                save_cover_thumbnail(book_id, raw, row["cover_href"])
                return Response(
                    content=raw,
                    media_type=ctype,
                    headers={"Cache-Control": "private, max-age=604800"},
                )
    return Response(content="Cover not found", status_code=404)


@app.get("/api/books/{book_id}/search")
def api_book_search(book_id: str, q: str = Query("")):
    q = q.strip()
    if not q:
        return {"results": []}
    ensure_book_downloaded_and_parsed(book_id)
    results = search_book_passages_fts(book_id, q, max_results=20)
    return {"results": results}


@app.post("/api/books/{book_id}/progress")
async def api_book_progress(book_id: str, request: Request):
    body = await _read_json_body(request)
    ch_idx = int(body.get("chapter_index", 0))
    scroll_pct = float(body.get("scroll_percent", 0.0))
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        row = conn.execute("SELECT manifest_json FROM books WHERE id = ?", (book_id,)).fetchone()
        manifest = None
        if row and row["manifest_json"]:
            try:
                manifest = json.loads(row["manifest_json"])
            except Exception:
                manifest = None
        ch_cnt, tot_words, ch_title, pct = compute_progress_summary(manifest, ch_idx, scroll_pct)
        with conn:
            conn.execute(
                """
                UPDATE books
                SET last_chapter_index = ?, scroll_percent = ?, last_opened_at = ?,
                    chapter_count = COALESCE(NULLIF(?, 0), chapter_count),
                    total_words = COALESCE(NULLIF(?, 0), total_words),
                    last_chapter_title = ?, progress_percent = ?
                WHERE id = ?
                """,
                (ch_idx, scroll_pct, now_iso, ch_cnt, tot_words, ch_title, pct, book_id),
            )
    finally:
        conn.close()
    return {"ok": True}


@app.post("/api/books/{book_id}/tags")
async def api_book_tags(book_id: str, request: Request):
    body = await _read_json_body(request)
    raw_tags = body.get("tags") or []
    clean_tags = []
    seen_tags = set()
    for t in raw_tags:
        ts = str(t).strip()
        if ts and ts.lower() not in seen_tags:
            seen_tags.add(ts.lower())
            clean_tags.append(ts)
    conn = get_db()
    try:
        with conn:
            conn.execute("UPDATE books SET tags = ? WHERE id = ?", (json.dumps(clean_tags), book_id))
    finally:
        conn.close()
    return {"ok": True, "tags": clean_tags}


# --- Bookmarks ---


@app.post("/api/books/{book_id}/bookmarks")
async def api_add_bookmark(book_id: str, request: Request):
    body = await _read_json_body(request)
    bm_id = body.get("id") or f"bm_{int(datetime.now().timestamp() * 1000)}"
    ch_idx = int(body.get("chapter_index", 0))
    ch_title = (body.get("chapter_title") or "").strip()
    scroll_pct = float(body.get("scroll_percent", 0.0))
    label = (body.get("label") or "").strip() or f"Section {ch_idx + 1} ({round(scroll_pct * 100)}%)"
    excerpt = (body.get("excerpt") or "").strip()[:240]
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO bookmarks (id, book_id, chapter_index, chapter_title, scroll_percent, label, excerpt, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    label = excluded.label,
                    scroll_percent = excluded.scroll_percent
                """,
                (bm_id, book_id, ch_idx, ch_title, scroll_pct, label, excerpt, now_iso),
            )
        bm_row = conn.execute("SELECT * FROM bookmarks WHERE id = ?", (bm_id,)).fetchone()
    finally:
        conn.close()
    return {"bookmark": dict(bm_row)}


@app.delete("/api/books/{book_id}/bookmarks/{bm_id}")
def api_delete_bookmark(book_id: str, bm_id: str):
    conn = get_db()
    try:
        with conn:
            conn.execute("DELETE FROM bookmarks WHERE id = ? AND book_id = ?", (bm_id, book_id))
    finally:
        conn.close()
    return {"ok": True}


# --- Annotations & Drive Sync ---


@app.post("/api/books/{book_id}/annotations")
async def api_upsert_annotation(book_id: str, request: Request):
    body = await _read_json_body(request)
    ann_id = body.get("id") or f"ann_{int(datetime.now().timestamp() * 1000)}"
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO annotations
                (id, book_id, chapter_index, chapter_href, chapter_title, page_number, selected_text, note, gemini_response, color, occurrence_index, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    page_number = COALESCE(NULLIF(excluded.page_number, ''), annotations.page_number),
                    note = excluded.note,
                    gemini_response = excluded.gemini_response,
                    color = excluded.color,
                    updated_at = excluded.updated_at
                """,
                (
                    ann_id,
                    book_id,
                    int(body.get("chapter_index", 0)),
                    body.get("chapter_href", ""),
                    body.get("chapter_title", ""),
                    body.get("page_number", ""),
                    body.get("selected_text", ""),
                    body.get("note", ""),
                    body.get("gemini_response", ""),
                    body.get("color", "yellow"),
                    int(body.get("occurrence_index", 0)),
                    now_iso,
                    now_iso,
                ),
            )
            row = conn.execute("SELECT * FROM annotations WHERE id = ?", (ann_id,)).fetchone()
            ann_dict = dict(row)
            upsert_annotation_fts(ann_dict, conn=conn)
    finally:
        conn.close()
    schedule_book_auto_sync(book_id)
    return {"annotation": ann_dict}


@app.delete("/api/books/{book_id}/annotations/{ann_id}")
def api_delete_annotation(book_id: str, ann_id: str):
    conn = get_db()
    try:
        with conn:
            conn.execute("DELETE FROM annotations WHERE id = ? AND book_id = ?", (ann_id, book_id))
            delete_annotation_fts(ann_id, conn=conn)
    finally:
        conn.close()
    schedule_book_auto_sync(book_id)
    return {"ok": True}


@app.post("/api/books/{book_id}/sync-drive")
def api_sync_drive(book_id: str):
    return sync_annotations_to_drive(book_id)


# --- Exports ---


@app.get("/api/books/{book_id}/export.md")
def api_export_md(book_id: str):
    conn = get_db()
    try:
        book_row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        ann_rows = conn.execute(
            "SELECT * FROM annotations WHERE book_id = ? ORDER BY chapter_index ASC, created_at ASC",
            (book_id,),
        ).fetchall()
    finally:
        conn.close()
    if not book_row:
        return JSONResponse({"error": "Book not found", "code": "ERROR"}, status_code=404)
    md_text = format_annotations_markdown(dict(book_row), [dict(r) for r in ann_rows])
    fname = companion_filename_for(book_row["name"], "-notes.md")
    return Response(
        content=md_text.encode("utf-8"),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.get("/api/books/{book_id}/export.bib")
def api_export_bib(book_id: str):
    conn = get_db()
    try:
        book_row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    finally:
        conn.close()
    if not book_row:
        return JSONResponse({"error": "Book not found", "code": "ERROR"}, status_code=404)
    bib_text = format_bibtex(dict(book_row))
    fname = companion_filename_for(book_row["name"], ".bib")
    return Response(
        content=bib_text.encode("utf-8"),
        media_type="application/x-bibtex; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'},
    )


@app.post("/api/books/{book_id}/export-gdoc")
def api_export_gdoc(book_id: str):
    res = export_notes_to_gdoc(book_id)
    return {"ok": True, "doc": res}


# --- Gemini Endpoints ---


@app.post("/api/books/{book_id}/argument-map")
async def api_argument_map(book_id: str, request: Request):
    body = await _read_json_body(request)
    ch_idx = int(body.get("chapter_index", 0))
    force = bool(body.get("force", False))

    if not force:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT map_json, model, created_at FROM argument_maps WHERE book_id = ? AND chapter_index = ?",
                (book_id, ch_idx),
            ).fetchone()
            if row:
                return {
                    "map": json.loads(row["map_json"]),
                    "model": row["model"],
                    "chapter_index": ch_idx,
                    "cached": True,
                    "created_at": row["created_at"],
                }
        finally:
            conn.close()

    book = ensure_book_downloaded_and_parsed(book_id)
    chapters = (book.get("manifest") or {}).get("chapters") or []
    if ch_idx < 0 or ch_idx >= len(chapters):
        return JSONResponse({"error": "Invalid chapter index", "code": "ERROR"}, status_code=400)
    ch = chapters[ch_idx]
    _, ch_text = get_chapter_plain_text(book_id, ch)
    if len(ch_text) > 50000:
        ch_text = ch_text[:50000] + "\n...[truncated]..."

    model = get_setting("gemini_model", "gemini-4-hydrogen")
    prompt = (
        f'Analyze the following chapter from "{book.get("title") or book.get("name")}" '
        f"(Section: {ch.get('title')}) and extract a structured argument map.\n\n"
        "Return ONLY raw JSON (no markdown code fences) with this exact schema:\n"
        "{\n"
        '  "thesis": "1-3 sentence synthesis of the chapter core thesis",\n'
        '  "key_claims": [{"claim": "...", "role": "Intervention|Evidence|Synthesis", "quote": "exact 8-18 word verbatim substring from the text"}],\n'
        '  "key_terms": [{"term": "...", "definition": "...", "quote": "exact 8-18 word verbatim substring from the text"}],\n'
        '  "critiques": [{"target": "...", "critique": "...", "quote": "exact 8-18 word verbatim substring from the text"}]\n'
        "}\n\n"
        "Every `quote` MUST be copied verbatim from the chapter text below so the reader can jump directly to it.\n\n"
        f"CHAPTER TEXT:\n{ch_text}"
    )
    res = call_gemini_interactions_api(prompt=prompt, model=model)
    raw_out = (res.get("output_text") or "").strip()
    raw_out = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw_out, flags=re.I | re.S).strip()
    try:
        parsed_map = json.loads(raw_out)
    except Exception:
        parsed_map = {"thesis": raw_out, "key_claims": [], "key_terms": [], "critiques": []}

    used_model = res.get("model", model)
    now_iso = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO argument_maps (book_id, chapter_index, map_json, model, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(book_id, chapter_index) DO UPDATE SET
                    map_json = excluded.map_json,
                    model = excluded.model,
                    created_at = excluded.created_at
                """,
                (book_id, ch_idx, json.dumps(parsed_map, ensure_ascii=False), used_model, now_iso),
            )
    finally:
        conn.close()

    return {"map": parsed_map, "model": used_model, "chapter_index": ch_idx, "cached": False, "created_at": now_iso}


@app.post("/api/gemini/compare")
async def api_gemini_compare(request: Request):
    body = await _read_json_body(request)
    book_ids = body.get("book_ids") or []
    topic = (body.get("topic") or "").strip()
    if len(book_ids) < 2 or not topic:
        return JSONResponse({"error": "Provide at least 2 book_ids and a topic", "code": "ERROR"}, status_code=400)

    conn = get_db()
    try:
        book_contexts = []
        for bid in book_ids[:3]:
            brow = conn.execute("SELECT * FROM books WHERE id = ?", (bid,)).fetchone()
            if not brow:
                continue
            bdict = dict(brow)
            anns = conn.execute(
                "SELECT chapter_title, page_number, color, selected_text, note FROM annotations WHERE book_id = ? LIMIT 15",
                (bid,),
            ).fetchall()
            ann_lines = [
                f'- [{a["color"]}] ({a["chapter_title"]} {a["page_number"] or ""}): "{a["selected_text"][:220]}"'
                + (f" — Note: {a['note']}" if a["note"] else "")
                for a in anns
            ]
            passages = []
            if bdict.get("downloaded"):
                hits = search_book_passages_fts(bid, topic, max_results=4)
                passages = [f"- ({h['chapter_title']}): {h['snippet'][:350]}" for h in hits]
            ctx = (
                f"BOOK: {bdict.get('title') or bdict.get('name')} by {bdict.get('author') or 'Unknown'}\n"
                "Highlights:\n" + ("\n".join(ann_lines) if ann_lines else "(none)") + "\n"
                "Relevant Passages:\n" + ("\n".join(passages) if passages else "(book not cached locally yet)")
            )
            book_contexts.append(ctx)
    finally:
        conn.close()

    model = get_setting("gemini_model", "gemini-4-hydrogen")
    prompt = (
        f'Compare how the following books approach the concept/question: "{topic}".\n'
        "Structure your response in clear Markdown with:\n"
        "1. Core Theses & Framings\n2. Key Points of Agreement & Divergence\n3. Cited Passages & Concepts from Each Work\n\n"
        + "\n\n---\n\n".join(book_contexts)
    )
    res = call_gemini_interactions_api(prompt=prompt, model=model)
    return {"comparison": res.get("output_text", ""), "model": res.get("model", model)}


@app.post("/api/books/{book_id}/report")
async def api_book_report(book_id: str, request: Request):
    body = await _read_json_body(request)
    mode = body.get("mode") or "structured"
    book = ensure_book_downloaded_and_parsed(book_id)
    conn = get_db()
    try:
        anns = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM annotations WHERE book_id = ? ORDER BY chapter_index ASC, created_at ASC",
                (book_id,),
            ).fetchall()
        ]
    finally:
        conn.close()

    if not anns:
        return JSONResponse(
            {"error": "No highlights found for this book yet. Highlight some passages first!", "code": "ERROR"},
            status_code=400,
        )

    grouped_lines = [
        f"# Reading Report: {book.get('title') or book.get('name')}",
        f"**Author:** {book.get('author') or 'Unknown'}  ",
        f"**Generated:** {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} · **Total Highlights:** {len(anns)}",
        "",
    ]

    for c_key, meta in HIGHLIGHT_SCHEMA.items():
        items = [a for a in anns if (a.get("color") or "yellow").lower() == c_key]
        grouped_lines.append(f"## {meta['label']} ({c_key.capitalize()} — {len(items)})")
        grouped_lines.append(f"*{meta['description']}*")
        grouped_lines.append("")
        if not items:
            grouped_lines.append("_No passages highlighted in this category yet._")
            grouped_lines.append("")
        else:
            for it in items:
                ch_label = it.get("chapter_title") or f"Section {it.get('chapter_index', 0) + 1}"
                pg_label = f", {it['page_number']}" if it.get("page_number") else ""
                grouped_lines.append(f'- **[{ch_label}{pg_label}]** "{it.get("selected_text", "").strip()}"')
                if it.get("note"):
                    grouped_lines.append(f"  - **Reader Note:** {it['note'].strip()}")
                if it.get("gemini_response"):
                    grouped_lines.append(f"  - **Gemini Note:** {it['gemini_response'].strip()}")
            grouped_lines.append("")

    structured_md = "\n".join(grouped_lines)

    if mode == "gemini":
        model = body.get("model") or get_setting("gemini_model", "gemini-4-hydrogen")
        sys_inst = (
            f'You are an expert academic research assistant generating a formal Reading Report for "{book.get("title")}" by {book.get("author")}.\n'
            "The reader uses a strict 5-color analytical coding system:\n"
            "- BLUE (Critical Points): Key interventions or important components of the overall argument.\n"
            "- YELLOW (Significant Points): Main ideas of paragraphs and supporting claims.\n"
            "- RED (Critiques): The author's critiques of other scholarship and interlocutors.\n"
            "- GREEN (Definitions): Key terms or operationalization of key variables.\n"
            "- GREY (Concerns): Passages the reader disagrees with or finds confusing/ambiguous.\n\n"
            "Synthesize the reader's color-coded highlights and notes into an executive scholarly Reading Report with dedicated sections:\n"
            "1. **Core Thesis & Critical Interventions (from Blue & Yellow highlights)**\n"
            "2. **Lexicon: Definitions & Operationalization of Key Variables (from Green highlights)**\n"
            "3. **Historiographical & Theoretical Critiques: What the Author Says About Other Scholars (from Red highlights)**\n"
            "4. **Critical Tensions, Concerns & Clarifications (addressing Grey highlights — unpack any confusing passages and evaluate the reader's disagreements)**\n"
            "Quote directly from the reader's highlights and cite the chapter/section titles."
        )
        try:
            gem_res = call_gemini_interactions_api(
                prompt=f"Here are all of the reader's color-coded highlights and notes for the book:\n\n{structured_md}\n\nPlease generate the comprehensive scholarly Reading Report.",
                system_instruction=sys_inst,
                model=model,
            )
            final_report = gem_res["output_text"] + "\n\n---\n\n" + structured_md
        except RuntimeError as err:
            if "MISSING_API_KEY" in str(err):
                return JSONResponse(
                    {
                        "error": "Gemini API key is required for AI synthesis. Use 'Instant Report' or set your Gemini API key in Settings.",
                        "code": "MISSING_API_KEY",
                    },
                    status_code=401,
                )
            raise
    else:
        final_report = structured_md

    set_setting(f"report_{book_id}", final_report)
    return {"report_markdown": final_report, "mode": mode}


def _build_gemini_chat_context(book_id: str, body: dict):
    question = (body.get("question") or "").strip()
    selected_text = (body.get("selected_text") or "").strip()
    action = body.get("action") or "ask"
    chapter_index = int(body.get("chapter_index", 0))
    scope = body.get("scope") or "chapter"
    prev_id = (body.get("previous_interaction_id") or "").strip()
    model = body.get("model") or get_setting("gemini_model", "gemini-4-hydrogen")

    book = ensure_book_downloaded_and_parsed(book_id)
    chapters = (book.get("manifest") or {}).get("chapters") or []
    ch_title, ch_text = ("", "")
    if 0 <= chapter_index < len(chapters):
        ch_title, ch_text = get_chapter_plain_text(book_id, chapters[chapter_index])

    conn = get_db()
    try:
        anns = [
            dict(r)
            for r in conn.execute(
                "SELECT chapter_title, color, selected_text, note FROM annotations WHERE book_id = ? ORDER BY chapter_index ASC, created_at ASC LIMIT 30",
                (book_id,),
            ).fetchall()
        ]
    finally:
        conn.close()

    sys_parts = [
        f'You are an insightful, rigorous academic and literary reading companion helping a reader study the book "{book.get("title")}" by {book.get("author")}.',
        f'Current Chapter ({chapter_index + 1}): "{ch_title}".',
        "Quote or reference specific concepts from the provided text accurately, and keep explanations clear, well-structured, and directly helpful.",
    ]

    context_blocks = []
    if selected_text:
        context_blocks.append(f'=== SELECTED PASSAGE ===\n"{selected_text}"')
    if scope in ("chapter", "book") and ch_text:
        context_blocks.append(f"=== CURRENT CHAPTER TEXT ({ch_title}) ===\n{ch_text[:24000]}")
    if scope == "book" and (question or selected_text):
        search_q = f"{question} {selected_text[:200]}".strip()
        matches = search_book_passages_fts(book_id, search_q, max_results=6)
        if matches:
            passages_str = "\n\n".join(f"[{m['chapter_title']}]: {m['snippet']}" for m in matches)
            context_blocks.append(f"=== RELEVANT PASSAGES ACROSS THE BOOK ===\n{passages_str}")
    if anns:
        ann_lines = []
        for a in anns[:25]:
            c_key = (a.get("color") or "yellow").lower()
            c_meta = HIGHLIGHT_SCHEMA.get(c_key, {"short": c_key.upper()})
            line = f'- [{c_key.upper()} / {c_meta["short"]}] ({a["chapter_title"]}): "{a["selected_text"][:260]}"'
            if a.get("note"):
                line += f" | Reader Note: {a['note']}"
            ann_lines.append(line)
        context_blocks.append(
            "=== READER'S COLOR-CODED HIGHLIGHTS & NOTES ===\n"
            "(System: Blue=Critical Points/Interventions, Yellow=Significant Main Ideas, Red=Author's Critiques of Other Scholarship, Green=Definitions/Operationalization, Grey=Reader's Concerns/Disagreements/Confusion)\n"
            + "\n".join(ann_lines)
        )

    if action == "explain" and not question:
        user_task = "Explain the selected passage in clear detail. Unpack its key terms, theoretical or narrative context within this chapter, and why it matters to the author's broader argument."
    elif action == "summarize" and not question:
        if selected_text:
            user_task = "Summarize the core argument and key takeaways of this selected passage concisely."
        else:
            user_task = f"Provide a structured summary of the key arguments, themes, and structure of this chapter ({ch_title})."
    elif action == "critique" and not question:
        user_task = "Analyze the assumptions, theoretical framing, and potential tensions or counterarguments in this passage."
    else:
        user_task = question or "Explain this passage."

    full_prompt = "\n\n".join(context_blocks + [f"=== READER REQUEST ===\n{user_task}"])
    return {
        "full_prompt": full_prompt,
        "system_instruction": "\n".join(sys_parts),
        "model": model,
        "prev_id": prev_id,
        "user_task": user_task,
        "selected_text": selected_text,
        "chapter_index": chapter_index,
    }


def _save_gemini_chat_turn(book_id: str, ctx: dict, gem_res: dict) -> dict:
    now_iso = datetime.now(timezone.utc).isoformat()
    ts_ms = int(datetime.now().timestamp() * 1000)
    user_msg_id = f"chat_{ts_ms}_u"
    asst_msg_id = f"chat_{ts_ms}_a"
    used_model = gem_res.get("model") or ctx["model"]

    conn = get_db()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO chats (id, book_id, chapter_index, role, content, selected_text, interaction_id, model, created_at)
                VALUES (?, ?, ?, 'user', ?, ?, ?, ?, ?)
                """,
                (user_msg_id, book_id, ctx["chapter_index"], ctx["user_task"], ctx["selected_text"], ctx["prev_id"], used_model, now_iso),
            )
            conn.execute(
                """
                INSERT INTO chats (id, book_id, chapter_index, role, content, selected_text, interaction_id, model, created_at)
                VALUES (?, ?, ?, 'assistant', ?, ?, ?, ?, ?)
                """,
                (asst_msg_id, book_id, ctx["chapter_index"], gem_res["output_text"], ctx["selected_text"], gem_res["id"], used_model, now_iso),
            )
    finally:
        conn.close()

    return {
        "interaction_id": gem_res["id"],
        "answer": gem_res["output_text"],
        "model": used_model,
        "user_message": {
            "id": user_msg_id,
            "role": "user",
            "content": ctx["user_task"],
            "selected_text": ctx["selected_text"],
            "chapter_index": ctx["chapter_index"],
            "created_at": now_iso,
        },
        "assistant_message": {
            "id": asst_msg_id,
            "role": "assistant",
            "content": gem_res["output_text"],
            "selected_text": ctx["selected_text"],
            "interaction_id": gem_res["id"],
            "model": used_model,
            "chapter_index": ctx["chapter_index"],
            "created_at": now_iso,
        },
    }


@app.post("/api/books/{book_id}/gemini/stream")
async def api_book_gemini_stream(book_id: str, request: Request):
    body = await _read_json_body(request)
    ctx = _build_gemini_chat_context(book_id, body)

    def _event_gen():
        try:
            final_res = None
            for ev_type, payload in stream_gemini_interactions_api(
                prompt=ctx["full_prompt"],
                system_instruction=ctx["system_instruction"],
                model=ctx["model"],
                previous_interaction_id=ctx["prev_id"],
            ):
                if ev_type == "delta":
                    yield f"data: {json.dumps({'type': 'delta', 'text': payload}, ensure_ascii=False)}\n\n"
                elif ev_type == "done":
                    final_res = payload
            if final_res is not None:
                saved = _save_gemini_chat_turn(book_id, ctx, final_res)
                yield f"data: {json.dumps({'type': 'done', **saved}, ensure_ascii=False)}\n\n"
        except RuntimeError as err:
            code = "MISSING_API_KEY" if "MISSING_API_KEY" in str(err) else "ERROR"
            msg = (
                "Gemini API key is required. Click the Settings (Key) button in the top bar to enter your GEMINI_API_KEY."
                if code == "MISSING_API_KEY"
                else str(err)
            )
            yield f"data: {json.dumps({'type': 'error', 'error': msg, 'code': code}, ensure_ascii=False)}\n\n"
        except Exception as exc:
            yield f"data: {json.dumps({'type': 'error', 'error': str(exc), 'code': 'ERROR'}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        _event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/books/{book_id}/gemini")
async def api_book_gemini(book_id: str, request: Request):
    body = await _read_json_body(request)
    ctx = _build_gemini_chat_context(book_id, body)
    try:
        gem_res = call_gemini_interactions_api(
            prompt=ctx["full_prompt"],
            system_instruction=ctx["system_instruction"],
            model=ctx["model"],
            previous_interaction_id=ctx["prev_id"],
        )
    except RuntimeError as err:
        if "MISSING_API_KEY" in str(err):
            return JSONResponse(
                {
                    "error": "Gemini API key is required. Click the Settings (Key) button in the top bar to enter your GEMINI_API_KEY.",
                    "code": "MISSING_API_KEY",
                },
                status_code=401,
            )
        raise
    return _save_gemini_chat_turn(book_id, ctx, gem_res)


@app.delete("/api/books/{book_id}/chats")
def api_delete_chats(book_id: str):
    conn = get_db()
    try:
        with conn:
            conn.execute("DELETE FROM chats WHERE book_id = ?", (book_id,))
    finally:
        conn.close()
    return {"ok": True}


# --- ZIP-Direct EPUB Resource Server ---


@app.get("/epub/{book_id}/{rel_path:path}")
def serve_epub_resource(book_id: str, rel_path: str):
    res = read_epub_resource(book_id, rel_path)
    if not res:
        return Response(content="EPUB resource not found", status_code=404)
    raw, ctype = res
    return Response(
        content=raw,
        media_type=ctype,
        headers={"Cache-Control": "private, max-age=86400"},
    )


@app.get("/pdf/{book_id}/book.pdf")
def serve_pdf_file(book_id: str):
    pdf_path = BOOKS_DIR / book_id / "book.pdf"
    if not pdf_path.is_file():
        ensure_book_downloaded_and_parsed(book_id)
    if not pdf_path.is_file():
        return Response(content="PDF not found", status_code=404)
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        headers={"Cache-Control": "private, max-age=86400"},
    )


# --- Static Frontend Files ---


@app.get("/")
@app.get("/index.html")
def serve_index():
    target = STATIC_DIR / "index.html"
    if not target.is_file():
        return Response(content="index.html not found", status_code=404)
    return Response(
        content=target.read_bytes(),
        media_type="text/html; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )


@app.get("/{rel_path:path}")
def serve_static(rel_path: str):
    target = (STATIC_DIR / rel_path).resolve()
    if not target.is_relative_to(STATIC_DIR.resolve()):
        return Response(content="Forbidden", status_code=403)
    if not target.is_file():
        return Response(content="File not found", status_code=404)
    ctype, _ = mimetypes.guess_type(str(target))
    return Response(
        content=target.read_bytes(),
        media_type=ctype or "text/plain; charset=utf-8",
        headers={"Cache-Control": "no-cache"},
    )
