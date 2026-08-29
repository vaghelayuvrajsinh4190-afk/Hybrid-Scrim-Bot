"""
FastAPI dashboard — main application entry point.

Run with:  uvicorn dashboard.main:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

# Ensure project root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from shared.config import DASHBOARD_SECRET_KEY, DASHBOARD_URL
from shared.database import ensure_indexes, close as db_close

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# App creation
# ---------------------------------------------------------------------------
app = FastAPI(title="Scrim Bot Dashboard", docs_url=None, redoc_url=None)

# Session middleware for OAuth2 flow
app.add_middleware(
    SessionMiddleware,
    secret_key=DASHBOARD_SECRET_KEY,
    session_cookie="scrimbot_session",
    max_age=86400,  # 24 hours
)

# ---------------------------------------------------------------------------
# Static files & templates
# ---------------------------------------------------------------------------
_base = Path(__file__).resolve().parent
app.mount("/static", StaticFiles(directory=_base / "static"), name="static")
templates = Jinja2Templates(directory=str(_base / "templates"))

# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------

@app.on_event("startup")
async def on_startup():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    await ensure_indexes()
    log.info("Dashboard started — %s", DASHBOARD_URL)


@app.on_event("shutdown")
async def on_shutdown():
    await db_close()

# ---------------------------------------------------------------------------
# Import routers
# ---------------------------------------------------------------------------
from dashboard.auth import router as auth_router
from dashboard.routers.overview import router as overview_router
from dashboard.routers.teams import router as teams_router
from dashboard.routers.groups import router as groups_router
from dashboard.routers.points import router as points_router
from dashboard.routers.verifications import router as verifications_router
from dashboard.routers.bans import router as bans_router
from dashboard.routers.channels import router as channels_router
from dashboard.routers.settings import router as settings_router

app.include_router(auth_router)
app.include_router(overview_router)
app.include_router(teams_router)
app.include_router(groups_router)
app.include_router(points_router)
app.include_router(verifications_router)
app.include_router(bans_router)
app.include_router(channels_router)
app.include_router(settings_router)


# ---------------------------------------------------------------------------
# Root redirect
# ---------------------------------------------------------------------------
@app.get("/")
async def root(request: Request):
    user = request.session.get("user")
    if user:
        return RedirectResponse("/overview")
    return RedirectResponse("/login")
