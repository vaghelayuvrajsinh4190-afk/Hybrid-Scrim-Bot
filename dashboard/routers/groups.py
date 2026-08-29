"""
Groups page — view group structure and channel assignments.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import GUILD_ID
from shared.database import groups_col
from dashboard.auth import get_current_user

router = APIRouter(tags=["groups"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/groups")
async def groups_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    groups = await groups_col().find(
        {"guild_id": GUILD_ID}
    ).sort("group_number", 1).to_list(length=50)

    return templates.TemplateResponse("groups.html", {
        "request": request,
        "user": user,
        "groups": groups,
        "page": "groups",
    })
