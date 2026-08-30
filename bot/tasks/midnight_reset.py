"""
Automated Daily Midnight Reset Task for Scrim Panels.

Supports per-panel admin customization (enable/disable, message purge,
team/slot wipe, role revocation, and progress bar reset).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import discord
from discord.ext import tasks

from shared.database import panels_col, points_col, registrations_col, teams_col

log = logging.getLogger(__name__)


async def reset_panel_data(guild: discord.Guild, panel: dict) -> None:
    """Execute reset operations on a single panel according to its configuration."""
    panel_id = panel.get("panel_id")
    guild_id = guild.id
    cfg = panel.get("midnight_reset", {})

    log.info("Resetting panel %s on guild %s (cfg=%s)", panel_id, guild.id, cfg)

    # 1. Clear Registrations and Teams
    if cfg.get("clear_teams", True):
        await registrations_col().delete_many({"guild_id": guild_id, "panel_id": panel_id})
        await teams_col().delete_many({"guild_id": guild_id, "panel_id": panel_id})

    # 2. Clear Points if configured
    if cfg.get("clear_points", False):
        await points_col().delete_many({"guild_id": guild_id, "panel_id": panel_id})

    # 3. Revoke Roles
    if cfg.get("clear_roles", True):
        role_id = panel.get("role_id")
        tag_role = guild.get_role(role_id) if role_id else None

        role_map = panel.get("channel_ids", {}).get("lobby_roles", {})
        idp_roles = [guild.get_role(rid) for rid in role_map.values() if guild.get_role(rid)]

        roles_to_revoke = [r for r in [tag_role, *idp_roles] if r]

        for member in guild.members:
            user_roles = [r for r in roles_to_revoke if r in member.roles]
            if user_roles:
                try:
                    await member.remove_roles(*user_roles, reason=f"Midnight reset for {panel_id}")
                    await asyncio.sleep(0.5)  # Rate-limit safety
                except Exception:
                    pass

    # 4. Clear/Purge Messages in tag, lobby, and conf channels
    if cfg.get("clear_messages", True):
        ch_ids = panel.get("channel_ids", {})
        channel_keys = [
            ch_ids.get("tag_channel_id"),
            ch_ids.get("conf_channel_id"),
            *list(ch_ids.get("lobby_channels", {}).values()),
        ]
        for cid in channel_keys:
            if not cid:
                continue
            channel = guild.get_channel(cid)
            if isinstance(channel, discord.TextChannel):
                try:
                    # Purge messages, keeping pinned ones
                    await channel.purge(limit=100, check=lambda m: not m.pinned)
                except Exception as e:
                    log.warning("Could not purge channel %s: %s", cid, e)

    # 5. Reset status and progress bars
    await panels_col().update_one(
        {"_id": panel["_id"]},
        {"$set": {
            "status": "pending",
            "ss_window_status": "closed",
            "ss_window_opened_at": None,
            "ss_window_closed_at": None,
        }},
    )


class MidnightResetTask:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self._last_reset_day: int = -1
        self.midnight_loop.start()

    def cog_unload(self) -> None:
        self.midnight_loop.cancel()

    @tasks.loop(minutes=1)
    async def midnight_loop(self) -> None:
        """Runs every minute and triggers when clock reaches 00:00 (Midnight IST)."""
        now_ist = datetime.now(ZoneInfo("Asia/Kolkata"))

        # Check if it's 00:00 and hasn't run today
        if now_ist.hour == 0 and now_ist.minute == 0 and self._last_reset_day != now_ist.day:
            self._last_reset_day = now_ist.day
            log.info("Starting Daily Midnight Scrims Reset...")

            cursor = panels_col().find({"midnight_reset.enabled": True})
            async for panel in cursor:
                guild = self.bot.get_guild(panel.get("guild_id"))
                if guild:
                    try:
                        await reset_panel_data(guild, panel)
                    except Exception as e:
                        log.error("Error during midnight reset for panel %s: %s", panel.get("panel_id"), e)

    @midnight_loop.before_loop
    async def before_midnight_loop(self) -> None:
        await self.bot.wait_until_ready()
