"""
Screenshot submission and review — !approve / !reject.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands

from shared.config import VERIFICATION_TTL_DAYS
from shared.database import panels_col, shared_channels_col, verifications_col

log = logging.getLogger(__name__)


class Screenshots(commands.Cog):
    """Handles screenshot submission and admin review."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        # Check if this is a screenshot submission channel
        # Screenshots go to the shared conf channel or a panel conf channel
        guild_id = message.guild.id

        # Check if it's a panel conf channel
        panel = await panels_col().find_one({
            "guild_id": guild_id,
            "channel_ids.conf_channel_id": message.channel.id,
        })

        if panel is None:
            return

        # Only process messages with image attachments
        images = [
            a for a in message.attachments
            if a.content_type and a.content_type.startswith("image/")
        ]
        if len(images) < 1:
            return

        # Look for a team name in the message content
        team_name = message.content.strip()
        if not team_name:
            await message.reply(
                "📸 Please include your **team name** with the screenshots.\n"
                "Example: `TeamName` (with images attached)",
                delete_after=10,
            )
            return

        # Check screenshot submission window
        screenshot_window = panel.get("screenshot_window_minutes", 30)
        match_start = panel.get("match_start_time")
        if match_start:
            now = datetime.now(timezone.utc)
            window_end = match_start + timedelta(minutes=screenshot_window)
            if now > window_end:
                await message.reply(
                    f"⏱️ Screenshot submission window has closed "
                    f"({screenshot_window} min after match start).",
                    delete_after=10,
                )
                return

        # Create verification doc
        screenshot_urls = [a.url for a in images]
        now = datetime.now(timezone.utc)

        await verifications_col().insert_one({
            "team_name": team_name,
            "guild_id": guild_id,
            "panel_id": panel["panel_id"],
            "window": panel["window"],
            "screenshot_urls": screenshot_urls,
            "submitted_by": message.author.id,
            "submitted_at": now,
            "status": "pending",
            "reviewed_by": None,
            "reviewed_at": None,
            "expires_at": None,  # Set on approval
        })

        await message.reply(
            f"📸 **{len(images)} screenshot(s)** submitted for team "
            f"**{team_name}** — awaiting admin review.\n"
            f"Admins: use `!approve {team_name}` or `!reject {team_name} [reason]`",
        )

    # ── !approve <team> ────────────────────────────────────────────────

    @commands.command(name="approve")
    @commands.has_permissions(administrator=True)
    async def approve_cmd(
        self, ctx: commands.Context, *, team_name: str
    ) -> None:
        guild_id = ctx.guild.id
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=VERIFICATION_TTL_DAYS)

        result = await verifications_col().update_one(
            {
                "guild_id": guild_id,
                "team_name": team_name,
                "status": "pending",
            },
            {
                "$set": {
                    "status": "approved",
                    "reviewed_by": ctx.author.id,
                    "reviewed_at": now,
                    "expires_at": expires,
                },
            },
        )

        if result.modified_count == 0:
            await ctx.reply(
                f"❌ No pending verification found for **{team_name}**.",
            )
            return

        await ctx.reply(f"✅ Screenshots for **{team_name}** approved!")

    # ── !reject <team> [reason] ────────────────────────────────────────

    @commands.command(name="reject")
    @commands.has_permissions(administrator=True)
    async def reject_cmd(
        self, ctx: commands.Context, *, args: str
    ) -> None:
        # Split: first word is team name, rest is reason
        parts = args.split(maxsplit=1)
        team_name = parts[0]
        reason = parts[1] if len(parts) > 1 else "No reason provided"

        guild_id = ctx.guild.id
        now = datetime.now(timezone.utc)

        result = await verifications_col().update_one(
            {
                "guild_id": guild_id,
                "team_name": team_name,
                "status": "pending",
            },
            {
                "$set": {
                    "status": "rejected",
                    "reviewed_by": ctx.author.id,
                    "reviewed_at": now,
                    # No expires_at — rejected docs stay for audit
                },
            },
        )

        if result.modified_count == 0:
            await ctx.reply(
                f"❌ No pending verification found for **{team_name}**.",
            )
            return

        await ctx.reply(
            f"❌ Screenshots for **{team_name}** rejected.\n"
            f"**Reason:** {reason}",
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Screenshots(bot))
