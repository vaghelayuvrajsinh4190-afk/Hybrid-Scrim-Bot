"""
Verifications page — pending/approved/rejected screenshot reviews.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from shared.config import GUILD_ID
from shared.database import verifications_col
from dashboard.auth import get_current_user

router = APIRouter(tags=["verifications"])
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


@router.get("/verifications")
async def verifications_page(request: Request, status: str = ""):
    user = get_current_user(request)
    if not user:
        return RedirectResponse("/login")

    guild_id = GUILD_ID
    query: dict = {"guild_id": guild_id}
    if status in ("pending", "approved", "rejected"):
        query["status"] = status

    verifications = await verifications_col().find(query).sort(
        "submitted_at", -1
    ).to_list(length=200)

    # Counts by status
    pending = await verifications_col().count_documents({
        "guild_id": guild_id, "status": "pending",
    })
    approved = await verifications_col().count_documents({
        "guild_id": guild_id, "status": "approved",
    })
    rejected = await verifications_col().count_documents({
        "guild_id": guild_id, "status": "rejected",
    })

    return templates.TemplateResponse("verifications.html", {
        "request": request,
        "user": user,
        "verifications": verifications,
        "counts": {"pending": pending, "approved": approved, "rejected": rejected},
        "current_status": status,
        "page": "verifications",
    })
