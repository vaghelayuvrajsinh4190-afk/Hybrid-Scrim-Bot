"""
Channels page — view/rename panel channels (with cooldown enforcement).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import GUILD_ID
from shared.database import panels_col, shared_channels_col
from dashboard.auth import require_admin

router = APIRouter(tags=["channels"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/channels")
async def channels_page(request: Request):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login")

    guild_id = GUILD_ID

    panels = await panels_col().find(
        {"guild_id": guild_id}
    ).to_list(length=50)

    shared = await shared_channels_col().find_one({"guild_id": guild_id})

    return templates.TemplateResponse("channels.html", {
        "request": request,
        "user": user,
        "panels": panels,
        "shared_channels": shared or {},
        "page": "channels",
    })
