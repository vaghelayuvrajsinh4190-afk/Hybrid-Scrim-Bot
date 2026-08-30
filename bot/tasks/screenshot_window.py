"""
Background task for monitoring and auto-opening/closing the 30-minute
screenshot submission window in lobby channels for IDP roles.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
import discord
from discord.ext import tasks

from shared.database import panels_col

log = logging.getLogger(__name__)


async def open_ss_window(bot: discord.Client, panel: dict) -> None:
    """Open screenshot window for all lobby channels in a panel."""
    guild = bot.get_guild(panel["guild_id"])
    if not guild:
        return

    now = datetime.now(timezone.utc)
    await panels_col().update_one(
        {"_id": panel["_id"]},
        {"$set": {"ss_window_status": "open", "ss_window_opened_at": now}},
    )

    ch_ids = panel.get("channel_ids", {})
    lobby_map = ch_ids.get("lobby_channels", {})
    role_map = ch_ids.get("lobby_roles", {})

    for gid, ch_id in lobby_map.items():
        ch = guild.get_channel(ch_id)
        r_id = role_map.get(gid)
        role = guild.get_role(r_id) if r_id else None
        if ch:
            if role:
                await ch.set_permissions(role, send_messages=True, view_channel=True)
            embed = discord.Embed(
                title="📸 Screenshot Submission is OPEN!",
                description=(
                    "Match completed! You have **30 minutes** to submit your match screenshots.\n"
                    "Post: `TeamName` + attach your result screenshots."
                ),
                colour=discord.Colour.green(),
                timestamp=now,
            )
            embed.set_footer(text="📌 NO SS = NO POINTS")
            await ch.send(embed=embed)

    log.info("Auto-opened SS window for panel %s", panel.get("panel_id"))


async def close_ss_window(bot: discord.Client, panel: dict) -> None:
    """Close screenshot window for all lobby channels in a panel."""
    guild = bot.get_guild(panel["guild_id"])
    if not guild:
        return

    now = datetime.now(timezone.utc)
    await panels_col().update_one(
        {"_id": panel["_id"]},
        {"$set": {"ss_window_status": "closed", "ss_window_closed_at": now}},
    )

    ch_ids = panel.get("channel_ids", {})
    lobby_map = ch_ids.get("lobby_channels", {})
    role_map = ch_ids.get("lobby_roles", {})

    for gid, ch_id in lobby_map.items():
        ch = guild.get_channel(ch_id)
        r_id = role_map.get(gid)
        role = guild.get_role(r_id) if r_id else None
        if ch:
            if role:
                await ch.set_permissions(role, send_messages=False, view_channel=True)
            embed = discord.Embed(
                title="🔒 Screenshot Submission is CLOSED",
                description="The 30-minute screenshot submission window has expired. Submissions are no longer accepted.",
                colour=discord.Colour.red(),
                timestamp=now,
            )
            await ch.send(embed=embed)

    log.info("Auto-closed SS window for panel %s", panel.get("panel_id"))


class ScreenshotWindowTask:
    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self.monitor_loop.start()

    def cog_unload(self) -> None:
        self.monitor_loop.cancel()

    @tasks.loop(minutes=1)
    async def monitor_loop(self) -> None:
        """Periodic loop to auto-open and auto-close SS windows."""
        now = datetime.now(timezone.utc)

        # 1. Auto-OPEN check
        cursor_open = panels_col().find({
            "ss_window_status": "closed",
            "match_start_time": {"$ne": None},
        })
        async for panel in cursor_open:
            mst = panel.get("match_start_time")
            if not mst:
                continue
            duration = panel.get("match_duration_minutes", 30)
            match_end = mst + timedelta(minutes=duration)
            if now >= match_end:
                # Open window
                await open_ss_window(self.bot, panel)

        # 2. Auto-CLOSE check
        cursor_close = panels_col().find({
            "ss_window_status": "open",
            "ss_window_opened_at": {"$ne": None},
        })
        async for panel in cursor_close:
            opened_at = panel.get("ss_window_opened_at")
            if not opened_at:
                continue
            window_len = panel.get("screenshot_window_minutes", 30)
            window_end = opened_at + timedelta(minutes=window_len)
            if now >= window_end:
                # Close window
                await close_ss_window(self.bot, panel)

    @monitor_loop.before_loop
    async def before_monitor_loop(self) -> None:
        await self.bot.wait_until_ready()
