"""Vercel ASGI entrypoint — proxies to backend FastAPI app.

Vercel auto-discovers this file when builds[].src points to api/index.py.
We add backend/ to sys.path so `from main import app` resolves all
internal imports (app.core, app.agents, etc.).

Key design decisions for Vercel compatibility:
- All heavy imports (agents, DB, LLM clients) happen inside main.app's lifespan,
  NOT at module import time — Vercel filesystem is read-only.
- If the app fails to import, we return a minimal ASGI app that reports the error.
"""

import sys
import traceback
from pathlib import Path

# Ensure backend/ is on sys.path so internal imports work
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Try to import the real app; fall back to an error-reporting app on failure
try:
    from main import app  # noqa: E402 — backend/main.py exposes app = FastAPI()
except Exception:
    # Build a minimal ASGI app that reports the import error.
    # This is more debuggable than Vercel's generic FUNCTION_INVOCATION_FAILED.
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    app = FastAPI(title="Flash Sale AI — Error")

    @app.get("/{path:path}")
    async def catch_all(path: str):
        return JSONResponse(
            status_code=500,
            content={
                "error": "App failed to import",
                "detail": traceback.format_exc()[-2000:],  # last 2000 chars
            },
        )

    @app.get("/health")
    def health():
        return {"status": "import_failed"}
