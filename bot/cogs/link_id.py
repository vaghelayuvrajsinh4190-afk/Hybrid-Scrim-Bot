"""
Link ID cog — persistent button for linking / updating a BGMI character ID.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from shared.database import shared_channels_col
from bot.utils.channel_ops import ensure_shared_channel
from bot.utils.checks import admin_only
from bot.views.persistent import LinkIDView

log = logging.getLogger(__name__)


class LinkIDCog(commands.Cog, name="LinkID"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="setup_linkid",
        description="Post the Link Your ID button in #verify-teamname.",
    )
    @admin_only()
    async def setup_linkid(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        guild_id = guild.id

        # Ensure the shared verify-teamname channel exists
        ch = await ensure_shared_channel(
            guild, "verify-teamname", guild_id,
            "verify_teamname_channel_id",
        )

        embed = discord.Embed(
            title="🔗 Link Your BGMI ID",
            description=(
                "Click the button below to link your 10-digit BGMI "
                "Character ID to your Discord account.\n\n"
                "If you've already linked, click to **update** it."
            ),
            colour=discord.Colour.teal(),
        )

        view = LinkIDView()
        await ch.send(embed=embed, view=view)

        await interaction.followup.send(
            f"✅ Link ID button posted in {ch.mention}.", ephemeral=True,
        )

    @app_commands.command(
        name="my_team",
        description="View your team status, slot, and points.",
    )
    async def my_team(self, interaction: discord.Interaction) -> None:
        from shared.database import teams_col, points_col, players_col

        guild_id = interaction.guild_id
        user_id = interaction.user.id

        # Find player info
        player = await players_col().find_one({
            "discord_id": user_id, "guild_id": guild_id,
        })

        # Find teams the player is on
        teams = await teams_col().find({
            "guild_id": guild_id,
            "members": user_id,
        }).to_list(length=20)

        if not teams and not player:
            await interaction.response.send_message(
                "❌ You have no linked ID or team registrations.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📊 Your Profile — {interaction.user.display_name}",
            colour=discord.Colour.purple(),
        )

        if player:
            embed.add_field(
                name="BGMI ID",
                value=player.get("bgmi_id", "Not linked"),
                inline=True,
            )

        for team in teams:
            members = " ".join(f"<@{m}>" for m in team.get("members", []))
            # Get team points
            total_pts = 0.0
            pts_cursor = points_col().find({
                "guild_id": guild_id,
                "team_name": team["team_name"],
                "panel_id": team["panel_id"],
                "window": team["window"],
            })
            async for pt in pts_cursor:
                total_pts += pt.get("total_points", 0)

            embed.add_field(
                name=f"🏆 {team['team_name']}",
                value=(
                    f"**Panel:** {team['panel_id']} | "
                    f"**Window:** {team['window']}\n"
                    f"**Slot:** #{team.get('slot_number', '?')} | "
                    f"**Points:** {total_pts:.0f}\n"
                    f"**Members:** {members}"
                ),
                inline=False,
            )

        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LinkIDCog(bot))
