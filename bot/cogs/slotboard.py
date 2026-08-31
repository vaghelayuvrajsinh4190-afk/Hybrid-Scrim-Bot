"""
Slot board cog — live slot board, cancel (with lock window), transfer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord.ext import commands

from shared.database import panels_col, registrations_col, teams_col
from bot.utils.progress_bar import make_circle_bar
from bot.views.slot_views import SlotBoardView

log = logging.getLogger(__name__)


class SlotBoardCog(commands.Cog, name="SlotBoard"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def refresh_board(self, guild_id: int, panel_id: str) -> None:
        """Refresh the live slot board embed for a panel."""
        panel = await panels_col().find_one({
            "guild_id": guild_id, "panel_id": panel_id,
        })
        if panel is None:
            return

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            return

        slotmng_ch_id = panel.get("channel_ids", {}).get("slotmng_channel_id")
        if not slotmng_ch_id:
            return

        ch = guild.get_channel(slotmng_ch_id)
        if not ch:
            return

        window = panel["window"]
        max_slots = panel.get("max_slots", 20)
        schedules = panel.get("schedules", [])

        # Determine reserved slots count (use first schedule or default)
        default_reserved = panel.get("default_reserved_slots", 0)

        # Fetch all teams for this panel+window
        teams = await teams_col().find({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
        }).sort("slot_number", 1).to_list(length=max_slots)

        # Build slot lines
        lines = []
        filled_slots = {}
        for team in teams:
            slot_num = team.get("slot_number", "?")
            filled_slots[slot_num] = team

        for i in range(1, max_slots + 1):
            if i in filled_slots:
                team = filled_slots[i]
                members = " ".join(f"<@{m}>" for m in team.get("members", []))
                is_reserved = team.get("is_reserved", False)
                if is_reserved:
                    # Admin-assigned reserved slot
                    lines.append(
                        f"`{i:>2}.` 🛡️ **{team['team_name']}** — {members}"
                    )
                else:
                    status = "✅" if team.get("confirmed") else "⏳"
                    lines.append(
                        f"`{i:>2}.` {status} **{team['team_name']}** — {members}"
                    )
            elif i <= default_reserved:
                # Unassigned reserved slot
                lines.append(f"`{i:>2}.` 🛡️ *Reserved*")
            else:
                lines.append(f"`{i:>2}.` 🔓 *Open*")

        # Circle progress bar
        filled_count = len(filled_slots)
        circle_bar = make_circle_bar(filled_count, max_slots, bar_len=10)
        bar_line = f"\n`{circle_bar}`  {filled_count}/{max_slots} filled"

        description = "\n".join(lines) if lines else "*No slots available.*"
        description += f"\n{bar_line}"

        embed = discord.Embed(
            title=f"📋 {panel_id.upper()} — Slot Board",
            description=description,
            colour=discord.Colour.blue(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(
            text=f"Window: {window} | {filled_count}/{max_slots} filled"
        )

        # Match start info
        match_start = panel.get("match_start_time")
        if match_start:
            embed.add_field(
                name="Match Start",
                value=discord.utils.format_dt(match_start, "R"),
                inline=True,
            )

        cancel_lock = panel.get("cancel_lock_minutes", 60)
        embed.add_field(
            name="Cancel Lock",
            value=f"{cancel_lock} min before match",
            inline=True,
        )

        # Update or create the slot board message
        msg_id = panel.get("slotboard_message_id")
        view = SlotBoardView()

        if msg_id:
            try:
                msg = await ch.fetch_message(msg_id)
                await msg.edit(embed=embed, view=view)
                return
            except discord.NotFound:
                pass

        # Create new message
        msg = await ch.send(embed=embed, view=view)
        await panels_col().update_one(
            {"guild_id": guild_id, "panel_id": panel_id},
            {"$set": {"slotboard_message_id": msg.id}},
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SlotBoardCog(bot))
