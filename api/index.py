"""Vercel ASGI entrypoint — proxies to backend FastAPI app.

Vercel auto-discovers this file (api/index.py is in its standard search path).
We add backend/ to sys.path so `from backend.main import app` resolves all
internal imports (app.core, app.agents, etc.).
"""

import sys
from pathlib import Path

# Ensure backend/ is on sys.path so internal imports work
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from main import app  # noqa: E402 — backend/main.py exposes app = FastAPI()
