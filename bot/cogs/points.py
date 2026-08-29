"""
Points system — /add_points, /pointtable, /postpointtable.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from shared.database import panels_col, points_col, shared_channels_col, teams_col
from bot.utils.checks import admin_only

log = logging.getLogger(__name__)


class Points(commands.Cog):
    """Points management — add, view, and post leaderboards."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    def _calc_points(
        self, kills: int, placement: int, points_table: dict
    ) -> float:
        """Calculate total points from kills + placement using the panel's table."""
        kill_weight = points_table.get("kill_weight", 1.0)
        placement_weights = points_table.get("placement_weights", {})
        placement_bonus = placement_weights.get(
            str(placement), placement_weights.get(placement, 0)
        )
        return (kills * kill_weight) + placement_bonus

    # ── /add_points ────────────────────────────────────────────────────

    @app_commands.command(
        name="add_points",
        description="Add match points for a team.",
    )
    @app_commands.describe(
        panel_id="Panel ID",
        team_name="Team name",
        kills="Number of kills",
        placement="Placement rank (1-16)",
        match_number="Match number (default 1)",
    )
    @admin_only()
    async def add_points(
        self,
        interaction: discord.Interaction,
        panel_id: str,
        team_name: str,
        kills: int,
        placement: int,
        match_number: int = 1,
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

        # Verify team exists
        team = await teams_col().find_one({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": panel["window"],
            "team_name": team_name,
        })
        if team is None:
            await interaction.followup.send(
                f"❌ Team **{team_name}** not found in {panel_id}/{panel['window']}.",
            )
            return

        pts_table = panel.get("points_table", {})
        total = self._calc_points(kills, placement, pts_table)

        await points_col().insert_one({
            "team_name": team_name,
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": panel["window"],
            "match_number": match_number,
            "kills": kills,
            "placement": placement,
            "total_points": total,
            "added_by": interaction.user.id,
            "added_at": datetime.now(timezone.utc),
        })

        await interaction.followup.send(
            f"✅ Added **{total:.0f} pts** to **{team_name}** "
            f"(Match #{match_number}: {kills} kills, #{placement} placement).",
        )

    # ── /pointtable ────────────────────────────────────────────────────

    @app_commands.command(
        name="pointtable",
        description="Show the current standings for a panel.",
    )
    @app_commands.describe(panel_id="Panel ID")
    async def pointtable(
        self, interaction: discord.Interaction, panel_id: str
    ) -> None:
        await interaction.response.defer()

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

        # Aggregate total points per team
        pipeline = [
            {
                "$match": {
                    "guild_id": guild_id,
                    "panel_id": panel_id,
                    "window": window,
                }
            },
            {
                "$group": {
                    "_id": "$team_name",
                    "total_points": {"$sum": "$total_points"},
                    "total_kills": {"$sum": "$kills"},
                    "matches": {"$sum": 1},
                }
            },
            {"$sort": {"total_points": -1}},
        ]

        results = await points_col().aggregate(pipeline).to_list(length=100)

        if not results:
            await interaction.followup.send(
                f"📊 No points recorded yet for **{panel_id}** ({window}).",
            )
            return

        # Build leaderboard embed
        embed = discord.Embed(
            title=f"📊 {panel_id} — Standings ({window})",
            colour=discord.Colour.gold(),
            timestamp=datetime.now(timezone.utc),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(results):
            prefix = medals[i] if i < 3 else f"`#{i + 1}`"
            lines.append(
                f"{prefix} **{r['_id']}** — "
                f"{r['total_points']:.0f} pts "
                f"({r['total_kills']} kills, {r['matches']} matches)"
            )

        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed)

    # ── /postpointtable ────────────────────────────────────────────────

    @app_commands.command(
        name="postpointtable",
        description="Post the leaderboard to #leaderboard.",
    )
    @app_commands.describe(panel_id="Panel ID")
    @admin_only()
    async def postpointtable(
        self, interaction: discord.Interaction, panel_id: str
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

        pipeline = [
            {
                "$match": {
                    "guild_id": guild_id,
                    "panel_id": panel_id,
                    "window": window,
                }
            },
            {
                "$group": {
                    "_id": "$team_name",
                    "total_points": {"$sum": "$total_points"},
                    "total_kills": {"$sum": "$kills"},
                    "best_placement": {"$min": "$placement"},
                    "matches": {"$sum": 1},
                }
            },
            {"$sort": {"total_points": -1}},
        ]

        results = await points_col().aggregate(pipeline).to_list(length=100)

        if not results:
            await interaction.followup.send(
                "📊 No points to post yet.",
            )
            return

        # Build podium embed
        embed = discord.Embed(
            title=f"🏆 {panel_id} — Final Leaderboard ({window})",
            colour=discord.Colour.gold(),
            timestamp=datetime.now(timezone.utc),
        )

        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, r in enumerate(results):
            prefix = medals[i] if i < 3 else f"`#{i + 1}`"
            lines.append(
                f"{prefix} **{r['_id']}**\n"
                f"   Points: **{r['total_points']:.0f}** • "
                f"Kills: **{r['total_kills']}** • "
                f"Best: **#{r['best_placement']}** • "
                f"Matches: **{r['matches']}**"
            )

        embed.description = "\n\n".join(lines)

        # Post to leaderboard channel
        from bot.utils.channel_ops import ensure_shared_channel

        lb_ch = await ensure_shared_channel(
            interaction.guild,
            "leaderboard",
            guild_id,
            "leaderboard_channel_id",
        )
        await lb_ch.send(embed=embed)

        await interaction.followup.send(
            f"✅ Leaderboard posted in {lb_ch.mention}.",
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Points(bot))
