#!/usr/bin/env python3
"""
Folio — Google Drive EPUB Reader & Gemini Study Companion
Entrypoint launching the FastAPI app via Uvicorn.
"""

from __future__ import annotations

import os
import uvicorn


def main():
    port = int(os.environ.get("PORT", 8765))
    host = os.environ.get(
        "HOST",
        "0.0.0.0" if (os.environ.get("PORT") or os.environ.get("RAILWAY_ENVIRONMENT")) else "127.0.0.1",
    )
    print(f"📚 Folio & Gemini EPUB Reader running at http://{host}:{port}", flush=True)
    uvicorn.run(
        "folio.app:app",
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )


if __name__ == "__main__":
    main()
