"""
Settings page — delete data controls with typed confirmation, panel config editing.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import GUILD_ID
from shared.database import (
    logs_col,
    panels_col,
    registrations_col,
    teams_col,
    verifications_col,
)
from dashboard.auth import require_admin

router = APIRouter(tags=["settings"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/settings")
async def settings_page(request: Request):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login")

    panels = await panels_col().find(
        {"guild_id": GUILD_ID}
    ).to_list(length=50)

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "user": user,
        "panels": panels,
        "page": "settings",
        "message": request.query_params.get("message", ""),
        "error": request.query_params.get("error", ""),
    })


# ── Destructive actions (typed confirmation required) ──────────────────────

@router.post("/settings/purge-screenshots")
async def purge_screenshots(
    request: Request, confirmation: str = Form(...)
):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login")

    if confirmation != "DELETE SCREENSHOTS":
        return RedirectResponse(
            "/settings?error=Confirmation+text+did+not+match.", status_code=303
        )

    result = await verifications_col().delete_many({"guild_id": GUILD_ID})
    return RedirectResponse(
        f"/settings?message=Purged+{result.deleted_count}+screenshot+records.",
        status_code=303,
    )


@router.post("/settings/clear-pending-claims")
async def clear_pending(
    request: Request, confirmation: str = Form(...)
):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login")

    if confirmation != "CLEAR CLAIMS":
        return RedirectResponse(
            "/settings?error=Confirmation+text+did+not+match.", status_code=303
        )

    result = await registrations_col().delete_many({
        "guild_id": GUILD_ID, "status": "pending",
    })
    return RedirectResponse(
        f"/settings?message=Cleared+{result.deleted_count}+pending+claims.",
        status_code=303,
    )


@router.post("/settings/clear-logs")
async def clear_logs(
    request: Request, confirmation: str = Form(...)
):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login")

    if confirmation != "CLEAR LOGS":
        return RedirectResponse(
            "/settings?error=Confirmation+text+did+not+match.", status_code=303
        )

    result = await logs_col().delete_many({"guild_id": GUILD_ID})
    return RedirectResponse(
        f"/settings?message=Cleared+{result.deleted_count}+log+entries.",
        status_code=303,
    )


@router.post("/settings/wipe-panel-data")
async def wipe_panel_data(
    request: Request,
    panel_id: str = Form(...),
    confirmation: str = Form(...),
):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login")

    panel_id = panel_id.upper()
    expected = f"WIPE {panel_id}"
    if confirmation != expected:
        return RedirectResponse(
            f"/settings?error=Type+'{expected}'+to+confirm.", status_code=303
        )

    guild_id = GUILD_ID
    panel = await panels_col().find_one({
        "guild_id": guild_id, "panel_id": panel_id,
    })
    if not panel:
        return RedirectResponse(
            f"/settings?error=Panel+{panel_id}+not+found.", status_code=303
        )

    reg_del = await registrations_col().delete_many({
        "guild_id": guild_id, "panel_id": panel_id,
    })
    team_del = await teams_col().delete_many({
        "guild_id": guild_id, "panel_id": panel_id,
    })

    return RedirectResponse(
        f"/settings?message=Wiped+{panel_id}:+{reg_del.deleted_count}+registrations,"
        f"+{team_del.deleted_count}+teams.",
        status_code=303,
    )
