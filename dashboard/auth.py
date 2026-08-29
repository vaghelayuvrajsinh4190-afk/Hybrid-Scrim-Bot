"""
Discord OAuth2 authentication — login, callback, logout, and session helpers.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse

from shared.config import (
    DASHBOARD_URL,
    DISCORD_CLIENT_ID,
    DISCORD_CLIENT_SECRET,
    GUILD_ID,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

DISCORD_API = "https://discord.com/api/v10"
OAUTH2_AUTHORIZE = "https://discord.com/api/oauth2/authorize"
OAUTH2_TOKEN = "https://discord.com/api/oauth2/token"
REDIRECT_URI = f"{DASHBOARD_URL}/callback"
SCOPES = "identify guilds guilds.members.read"


def get_current_user(request: Request) -> Optional[dict]:
    """Return the session user dict or None."""
    return request.session.get("user")


def require_admin(request: Request) -> Optional[dict]:
    """Return user dict if logged in and admin, else None."""
    user = get_current_user(request)
    if user and user.get("is_admin"):
        return user
    return None


# ── Login redirect ─────────────────────────────────────────────────────────

@router.get("/login")
async def login(request: Request):
    from pathlib import Path
    from fastapi.templating import Jinja2Templates

    templates = Jinja2Templates(
        directory=str(Path(__file__).resolve().parent / "templates")
    )
    return templates.TemplateResponse("login.html", {"request": request})


@router.get("/auth/discord")
async def auth_discord():
    """Redirect user to Discord OAuth2 consent screen."""
    url = (
        f"{OAUTH2_AUTHORIZE}"
        f"?client_id={DISCORD_CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={SCOPES.replace(' ', '%20')}"
    )
    return RedirectResponse(url)


# ── OAuth2 callback ───────────────────────────────────────────────────────

@router.get("/callback")
async def callback(request: Request, code: str):
    """Exchange code for token, fetch user + guild membership."""
    async with httpx.AsyncClient() as client:
        # Exchange code → token
        token_resp = await client.post(
            OAUTH2_TOKEN,
            data={
                "client_id": DISCORD_CLIENT_ID,
                "client_secret": DISCORD_CLIENT_SECRET,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "scope": SCOPES,
            },
        )
        if token_resp.status_code != 200:
            log.error("Token exchange failed: %s", token_resp.text)
            return RedirectResponse("/login?error=token_failed")

        token_data = token_resp.json()
        access_token = token_data["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # Fetch user identity
        user_resp = await client.get(f"{DISCORD_API}/users/@me", headers=headers)
        if user_resp.status_code != 200:
            return RedirectResponse("/login?error=user_failed")
        user_data = user_resp.json()

        # Fetch guild member to check roles
        is_admin = False
        if GUILD_ID:
            member_resp = await client.get(
                f"{DISCORD_API}/users/@me/guilds/{GUILD_ID}/member",
                headers=headers,
            )
            if member_resp.status_code == 200:
                member_data = member_resp.json()
                # Check for administrator permission via roles
                # For simplicity, we check if the member has any role
                # that grants admin — or we can fetch guild roles.
                # Here we store the roles and check against guild settings later.
                # Simple approach: check guild permissions bit
                permissions = int(member_data.get("permissions", 0))
                is_admin = bool(permissions & 0x8)  # ADMINISTRATOR bit

                if not is_admin:
                    return RedirectResponse("/login?error=not_admin")
            else:
                return RedirectResponse("/login?error=not_member")

        # Store in session
        avatar_hash = user_data.get("avatar", "")
        avatar_url = (
            f"https://cdn.discordapp.com/avatars/{user_data['id']}/{avatar_hash}.png"
            if avatar_hash
            else "https://cdn.discordapp.com/embed/avatars/0.png"
        )

        request.session["user"] = {
            "id": user_data["id"],
            "username": user_data.get("username", "Unknown"),
            "global_name": user_data.get("global_name", ""),
            "avatar_url": avatar_url,
            "is_admin": is_admin,
        }

    return RedirectResponse("/overview")


# ── Logout ─────────────────────────────────────────────────────────────────

@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login")
