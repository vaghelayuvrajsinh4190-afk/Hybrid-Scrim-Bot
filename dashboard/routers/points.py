"""
Points page — leaderboard with per-panel filtering.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import GUILD_ID
from shared.database import panels_col, points_col
from dashboard.auth import get_current_user

router = APIRouter(tags=["points"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/points")
async def points_page(request: Request, panel_id: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    guild_id = GUILD_ID
    panels = await panels_col().find({"guild_id": guild_id}).to_list(length=50)

    standings = []
    selected_panel = None

    if panel_id:
        panel_id = panel_id.upper()
        selected_panel = await panels_col().find_one({
            "guild_id": guild_id, "panel_id": panel_id,
        })
        if selected_panel:
            pipeline = [
                {
                    "$match": {
                        "guild_id": guild_id,
                        "panel_id": panel_id,
                        "window": selected_panel["window"],
                    },
                },
                {
                    "$group": {
                        "_id": "$team_name",
                        "total_points": {"$sum": "$total_points"},
                        "total_kills": {"$sum": "$kills"},
                        "best_placement": {"$min": "$placement"},
                        "matches": {"$sum": 1},
                    },
                },
                {"$sort": {"total_points": -1}},
            ]
            standings = await points_col().aggregate(pipeline).to_list(length=100)

    return templates.TemplateResponse("points.html", {
        "request": request,
        "user": user,
        "panels": panels,
        "standings": standings,
        "current_panel": panel_id,
        "selected_panel": selected_panel,
        "page": "points",
    })
