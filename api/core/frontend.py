"""
Serving the built web UI (``web/dist``) from the API.

On a show Pi the React app is built once (``npm run build``) and served by
FastAPI on the API's own port, so the UI and API share one origin (no CORS)
and one tunnel target. In development the Vite dev server is used instead and
``web/dist`` usually doesn't exist; then nothing here is registered and ``/``
keeps returning the API info JSON.

Only files inside the dist directory are ever served. Paths under ``/api``
are never answered by the single-page-app fallback, so an unknown API path is
still a JSON 404 rather than the app's HTML.
"""

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]

# Paths FastAPI itself owns; the fallback must never shadow them.
RESERVED_PREFIXES = ("api", "docs", "redoc", "openapi.json")

# index.html names the hashed asset files, so it must not be cached or a new
# build won't be picked up; the hashed assets themselves can be cached forever.
NO_CACHE = {"Cache-Control": "no-cache"}


def resolve_dist_dir(configured: str = "") -> Path:
    """The web build directory: ``[api] web_dist`` if set, else ``<repo>/web/dist``."""
    if configured:
        return Path(os.path.expandvars(os.path.expanduser(configured))).resolve()
    return REPO_ROOT / "web" / "dist"


def frontend_index(dist_dir: Path) -> Optional[Path]:
    """``index.html`` in ``dist_dir``, or None when there is no build."""
    index = dist_dir / "index.html"
    return index if index.is_file() else None


def index_response(index: Path) -> FileResponse:
    """The app's ``index.html``, uncached."""
    return FileResponse(index, media_type="text/html", headers=NO_CACHE)


def _is_reserved(path: str) -> bool:
    first = path.split("/", 1)[0]
    return first in RESERVED_PREFIXES


def add_frontend_routes(app: FastAPI, dist_dir: Path) -> Optional[Path]:
    """
    Serve the web build in ``dist_dir`` from ``app``, if there is one.

    Registers ``/assets`` as static files and a catch-all GET that returns a
    file from the build when one matches, else ``index.html`` (client-side
    routes such as ``/show``). Call it after every API route is registered:
    routes match in order, so the catch-all must come last.

    Returns the index path when the UI is served, None when there's no build.
    """
    dist_root = dist_dir.resolve()
    index = frontend_index(dist_root)
    if index is None:
        log.info(f"No web build at {dist_root}; serving the API only "
                 "(run `npm run build` in web/ to serve the UI here)")
        return None

    assets = dist_root / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        if _is_reserved(full_path):
            raise HTTPException(status_code=404, detail="Not Found")
        if full_path:
            candidate = (dist_root / full_path).resolve()
            if candidate.is_relative_to(dist_root) and candidate.is_file():
                if candidate == index:
                    return index_response(index)
                return FileResponse(candidate)
        return index_response(index)

    log.info(f"Serving the web UI from {dist_root}")
    return index
