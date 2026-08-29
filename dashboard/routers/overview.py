"""
Overview page — guild stats, active panels, recent activity.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import GUILD_ID
from shared.database import (
    bans_col,
    logs_col,
    panels_col,
    players_col,
    registrations_col,
    teams_col,
    verifications_col,
)
from dashboard.auth import get_current_user

router = APIRouter(tags=["overview"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/overview")
async def overview_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    guild_id = GUILD_ID

    # Gather stats
    panel_count = await panels_col().count_documents({"guild_id": guild_id})
    team_count = await teams_col().count_documents({"guild_id": guild_id})
    player_count = await players_col().count_documents({"guild_id": guild_id})
    active_regs = await registrations_col().count_documents({
        "guild_id": guild_id, "status": "pending",
    })
    ban_count = await bans_col().count_documents({"guild_id": guild_id})
    pending_verifications = await verifications_col().count_documents({
        "guild_id": guild_id, "status": "pending",
    })

    # Recent logs
    recent_logs = await logs_col().find(
        {"guild_id": guild_id}
    ).sort("timestamp", -1).limit(10).to_list(length=10)

    # Active panels
    panels = await panels_col().find(
        {"guild_id": guild_id}
    ).to_list(length=50)

    return templates.TemplateResponse("overview.html", {
        "request": request,
        "user": user,
        "stats": {
            "panels": panel_count,
            "teams": team_count,
            "players": player_count,
            "active_claims": active_regs,
            "bans": ban_count,
            "pending_verifications": pending_verifications,
        },
        "recent_logs": recent_logs,
        "panels": panels,
        "page": "overview",
    })
