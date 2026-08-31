"""
Moderation — ban/unban, fake tag check, and manual team management.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from shared.database import (
    bans_col,
    logs_col,
    panels_col,
    registrations_col,
    teams_col,
)
from bot.utils.checks import admin_only

log = logging.getLogger(__name__)


class Moderation(commands.Cog):
    """Admin moderation tools: ban, unban, remove team, fake tag."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(name="clear", description="Remove a specified number of messages from the channel.")
    @app_commands.describe(amount="The number of messages to delete (default: 100, max: 100)")
    @admin_only()
    async def clear_messages(self, interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100] = 100) -> None:
        """Purge messages from the current channel."""
        await interaction.response.defer(ephemeral=True)
        try:
            # Prevent deleting messages older than 14 days to avoid 429 rate limits
            fourteen_days_ago = discord.utils.utcnow() - timedelta(days=14)
            def is_recent(m):
                return m.created_at > fourteen_days_ago

            deleted = await interaction.channel.purge(limit=amount, check=is_recent, bulk=True)
            await interaction.followup.send(f"✅ Deleted {len(deleted)} recent messages.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ I do not have permission to manage messages in this channel.", ephemeral=True)
        except discord.HTTPException:
            await interaction.followup.send("❌ Failed to delete messages.", ephemeral=True)

    async def _log_action(
        self,
        guild_id: int,
        action: str,
        actor_id: int,
        details: dict | None = None,
    ) -> None:
        """Write an audit log entry."""
        await logs_col().insert_one({
            "guild_id": guild_id,
            "action": action,
            "actor_discord_id": actor_id,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc),
        })

    # ── /ban ───────────────────────────────────────────────────────────

    @app_commands.command(name="ban", description="Ban a player from scrims.")
    @app_commands.describe(
        user="Player to ban",
        duration="Ban duration in hours (0 = permanent)",
        reason="Ban reason",
    )
    @admin_only()
    async def ban_cmd(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        duration: int = 0,
        reason: str = "No reason provided",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        guild_id = interaction.guild_id
        now = datetime.now(timezone.utc)
        expires = now + timedelta(hours=duration) if duration > 0 else None

        # Upsert ban
        await bans_col().update_one(
            {"discord_id": user.id, "guild_id": guild_id},
            {
                "$set": {
                    "reason": reason,
                    "banned_by": interaction.user.id,
                    "banned_at": now,
                    "expires_at": expires,
                },
            },
            upsert=True,
        )

        await self._log_action(
            guild_id,
            "ban",
            interaction.user.id,
            {
                "target": user.id,
                "duration_hours": duration,
                "reason": reason,
            },
        )

        dur_text = f"{duration} hours" if duration else "permanent"
        await interaction.followup.send(
            f"🚫 **{user.display_name}** has been banned ({dur_text}).\n"
            f"**Reason:** {reason}",
        )

    # ── /unban ─────────────────────────────────────────────────────────

    @app_commands.command(
        name="unban", description="Remove a ban from a player."
    )
    @app_commands.describe(user="Player to unban")
    @admin_only()
    async def unban_cmd(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        result = await bans_col().delete_one({
            "discord_id": user.id,
            "guild_id": interaction.guild_id,
        })

        if result.deleted_count == 0:
            await interaction.followup.send(
                f"ℹ️ **{user.display_name}** was not banned.",
            )
            return

        await self._log_action(
            interaction.guild_id,
            "unban",
            interaction.user.id,
            {"target": user.id},
        )

        await interaction.followup.send(
            f"✅ **{user.display_name}** has been unbanned.",
        )

    # ── /remove_team ───────────────────────────────────────────────────

    @app_commands.command(
        name="remove_team",
        description="Remove a team and its registration (bypasses cancel lock).",
    )
    @app_commands.describe(
        panel_id="Panel ID",
        team_name="Team name to remove",
    )
    @admin_only()
    async def remove_team_cmd(
        self,
        interaction: discord.Interaction,
        panel_id: str,
        team_name: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        panel_id = panel_id.upper()
        guild_id = interaction.guild_id

        # Delete team
        team_result = await teams_col().delete_many({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "team_name": team_name,
        })

        # Delete associated registration
        reg_result = await registrations_col().delete_many({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "team_name": team_name,
        })

        if team_result.deleted_count == 0 and reg_result.deleted_count == 0:
            await interaction.followup.send(
                f"❌ Team **{team_name}** not found in **{panel_id}**.",
            )
            return

        await self._log_action(
            guild_id,
            "remove_team",
            interaction.user.id,
            {"panel_id": panel_id, "team_name": team_name},
        )

        await interaction.followup.send(
            f"✅ Team **{team_name}** removed from **{panel_id}**.",
        )

        # Refresh slot board
        cog = self.bot.get_cog("SlotBoard")
        if cog and hasattr(cog, "refresh_board"):
            await cog.refresh_board(guild_id, panel_id)

    # ── /clear_registration ────────────────────────────────────────────

    @app_commands.command(
        name="clear_registration",
        description="Clear a player's registration for the current window.",
    )
    @app_commands.describe(
        panel_id="Panel ID",
        user="Player whose registration to clear",
    )
    @admin_only()
    async def clear_reg_cmd(
        self,
        interaction: discord.Interaction,
        panel_id: str,
        user: discord.Member,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        panel_id = panel_id.upper()
        guild_id = interaction.guild_id

        panel = await panels_col().find_one({
            "guild_id": guild_id, "panel_id": panel_id,
        })
        if panel is None:
            await interaction.followup.send(
                f"❌ Panel **{panel_id}** not found.",
            )
            return

        window = panel["window"]

        # Remove registrations
        await registrations_col().delete_many({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
            "claimer_discord_id": user.id,
        })

        # Remove teams owned by this user
        await teams_col().delete_many({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
            "owner_discord_id": user.id,
        })

        # Revoke role
        role_id = panel.get("role_id")
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                try:
                    await user.remove_roles(role, reason="Registration cleared")
                except discord.Forbidden:
                    pass

        await self._log_action(
            guild_id,
            "clear_registration",
            interaction.user.id,
            {
                "panel_id": panel_id,
                "window": window,
                "target": user.id,
            },
        )

        await interaction.followup.send(
            f"✅ Registration cleared for **{user.display_name}** "
            f"in **{panel_id}** ({window}).",
        )

        cog = self.bot.get_cog("SlotBoard")
        if cog and hasattr(cog, "refresh_board"):
            await cog.refresh_board(guild_id, panel_id)

    # ── /faketag ───────────────────────────────────────────────────────

    @app_commands.command(
        name="faketag",
        description="Flag a team for fake tag review.",
    )
    @app_commands.describe(
        panel_id="Panel ID",
        team_name="Team name to flag",
        reason="Reason for flagging",
    )
    @admin_only()
    async def faketag_cmd(
        self,
        interaction: discord.Interaction,
        panel_id: str,
        team_name: str,
        reason: str = "Suspected fake tag",
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        panel_id = panel_id.upper()
        guild_id = interaction.guild_id

        team = await teams_col().find_one({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "team_name": team_name,
        })
        if team is None:
            await interaction.followup.send(
                f"❌ Team **{team_name}** not found in **{panel_id}**.",
            )
            return

        await self._log_action(
            guild_id,
            "fake_tag_flag",
            interaction.user.id,
            {
                "panel_id": panel_id,
                "team_name": team_name,
                "reason": reason,
                "members": team.get("members", []),
            },
        )

        # Post alert to admin channel
        from bot.utils.channel_ops import ensure_shared_channel

        admin_ch = await ensure_shared_channel(
            interaction.guild,
            "admin-logs",
            guild_id,
            "admin_channel_id",
        )

        members_str = " ".join(f"<@{m}>" for m in team.get("members", []))
        alert_embed = discord.Embed(
            title="⚠️ Fake Tag Flag",
            description=(
                f"**Team:** {team_name}\n"
                f"**Panel:** {panel_id}\n"
                f"**Members:** {members_str}\n"
                f"**Reason:** {reason}\n"
                f"**Flagged by:** <@{interaction.user.id}>"
            ),
            colour=discord.Colour.orange(),
            timestamp=datetime.now(timezone.utc),
        )
        await admin_ch.send(embed=alert_embed)

        await interaction.followup.send(
            f"⚠️ Team **{team_name}** flagged for fake tag review.",
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
