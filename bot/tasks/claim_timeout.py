"""
Background task — claim timeout auto-release.

Runs on a loop checking for pending registrations whose deadline has
passed.  Deadlines are stored in Mongo so this survives bot restarts.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord.ext import tasks

from shared.config import CLAIM_CHECK_INTERVAL_SECONDS
from shared.database import panels_col, registrations_col

log = logging.getLogger(__name__)


class ClaimTimeoutTask:
    """Manages the periodic claim-expiry background loop."""

    def __init__(self, bot: discord.Client) -> None:
        self.bot = bot
        self._loop = self._create_loop()

    def _create_loop(self):
        @tasks.loop(seconds=CLAIM_CHECK_INTERVAL_SECONDS)
        async def _check_expired_claims():
            await self._expire_claims()

        @_check_expired_claims.before_loop
        async def _wait_ready():
            await self.bot.wait_until_ready()

        return _check_expired_claims

    def start(self) -> None:
        if not self._loop.is_running():
            self._loop.start()

    def stop(self) -> None:
        if self._loop.is_running():
            self._loop.cancel()

    async def _expire_claims(self) -> None:
        now = datetime.now(timezone.utc)

        cursor = registrations_col().find({
            "status": "pending",
            "claim_deadline": {"$lte": now},
        })

        async for reg in cursor:
            guild_id = reg["guild_id"]
            panel_id = reg["panel_id"]
            user_id = reg["claimer_discord_id"]

            # Mark expired
            await registrations_col().update_one(
                {"_id": reg["_id"]},
                {"$set": {"status": "expired"}},
            )

            # Revoke role
            panel = await panels_col().find_one({
                "guild_id": guild_id, "panel_id": panel_id,
            })
            if panel:
                guild = self.bot.get_guild(guild_id)
                if guild:
                    role_id = panel.get("role_id")
                    if role_id:
                        role = guild.get_role(role_id)
                        member = guild.get_member(user_id)
                        if role and member:
                            try:
                                await member.remove_roles(
                                    role, reason="Claim expired",
                                )
                            except discord.Forbidden:
                                pass

                    # Post notice in tag channel
                    tag_ch_id = panel.get("channel_ids", {}).get(
                        "tag_channel_id",
                    )
                    if tag_ch_id:
                        ch = guild.get_channel(tag_ch_id)
                        if ch:
                            try:
                                await ch.send(
                                    f"⏱️ <@{user_id}>'s claim expired "
                                    f"— slot released.",
                                )
                            except discord.Forbidden:
                                pass

            log.info(
                "Expired claim for user %s in panel %s/%s",
                user_id, guild_id, panel_id,
            )

            # Refresh slot board
            cog = self.bot.get_cog("SlotBoard")  # type: ignore[attr-defined]
            if cog and hasattr(cog, "refresh_board"):
                await cog.refresh_board(guild_id, panel_id)
