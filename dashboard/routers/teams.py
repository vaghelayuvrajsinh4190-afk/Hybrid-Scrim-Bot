"""
Teams page — browse/search teams, view members, registration status.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import GUILD_ID
from shared.database import panels_col, registrations_col, teams_col
from dashboard.auth import get_current_user

router = APIRouter(tags=["teams"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/teams")
async def teams_page(
    request: Request,
    panel_id: str = "",
    search: str = "",
):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    guild_id = GUILD_ID
    query: dict = {"guild_id": guild_id}

    if panel_id:
        query["panel_id"] = panel_id.upper()
    if search:
        query["team_name"] = {"$regex": search, "$options": "i"}

    teams = await teams_col().find(query).sort("registered_at", -1).to_list(length=200)

    # Get panels for the filter dropdown
    panels = await panels_col().find({"guild_id": guild_id}).to_list(length=50)

    # Get registration status for each team
    for team in teams:
        reg = await registrations_col().find_one({
            "guild_id": guild_id,
            "panel_id": team["panel_id"],
            "window": team["window"],
            "team_name": team.get("team_name"),
        })
        team["reg_status"] = reg.get("status", "unknown") if reg else "unknown"

    return templates.TemplateResponse("teams.html", {
        "request": request,
        "user": user,
        "teams": teams,
        "panels": panels,
        "current_panel": panel_id,
        "search": search,
        "page": "teams",
    })
