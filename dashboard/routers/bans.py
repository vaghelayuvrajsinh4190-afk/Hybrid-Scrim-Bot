"""
Bans page — active bans, history, add/remove.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import GUILD_ID
from shared.database import bans_col
from dashboard.auth import require_admin

router = APIRouter(tags=["bans"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/bans")
async def bans_page(request: Request):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login")

    bans = await bans_col().find(
        {"guild_id": GUILD_ID}
    ).sort("banned_at", -1).to_list(length=200)

    return templates.TemplateResponse("bans.html", {
        "request": request,
        "user": user,
        "bans": bans,
        "page": "bans",
    })


@router.post("/bans/add")
async def add_ban(
    request: Request,
    discord_id: int = Form(...),
    reason: str = Form(""),
    duration_hours: int = Form(0),
):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(hours=duration_hours) if duration_hours > 0 else None

    await bans_col().update_one(
        {"discord_id": discord_id, "guild_id": GUILD_ID},
        {
            "$set": {
                "reason": reason,
                "banned_by": int(user["id"]),
                "banned_at": now,
                "expires_at": expires,
            },
        },
        upsert=True,
    )

    return RedirectResponse("/bans", status_code=303)


@router.post("/bans/remove")
async def remove_ban(request: Request, discord_id: int = Form(...)):
    user = require_admin(request)
    if not user:
        return RedirectResponse("/login")

    await bans_col().delete_one({
        "discord_id": discord_id, "guild_id": GUILD_ID,
    })

    return RedirectResponse("/bans", status_code=303)
