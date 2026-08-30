"""
Slot board view — Cancel and Transfer buttons scoped to the claiming team.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import ui

from shared.database import panels_col, registrations_col, teams_col

log = logging.getLogger(__name__)


class SlotBoardView(ui.View):
    """Persistent view on the slot-management embed.

    Contains Cancel and Transfer buttons.  Each action is scoped:
    only the team owner (claimer) can use them on their own slot.
    """

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(
        label="❌ Cancel Slot",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:cancel_slot",
    )
    async def cancel_slot_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        # Find ALL active registrations for the user
        regs = await registrations_col().find({
            "guild_id": guild_id,
            "claimer_discord_id": user_id,
            "status": {"$in": ["pending", "completed"]},
        }).to_list(25)

        if not regs:
            await interaction.response.send_message(
                "❌ You don't have a slot to cancel.", ephemeral=True,
            )
            return

        # Import shared cancel logic
        from bot.views.persistent import _execute_cancel, CancelSlotSelectView

        if len(regs) == 1:
            # Single slot — cancel directly
            await _execute_cancel(interaction, regs[0], regs[0]["panel_id"])
            return

        # Multiple slots — show selection dropdown
        options = []
        for r in regs:
            gid = r.get("group_id", "G01")
            panel_id = r.get("panel_id", "?")
            team_name = r.get("team_name") or "(pending)"
            options.append(discord.SelectOption(
                label=f"{panel_id} {gid} — {team_name}",
                value=str(r["_id"]),
                description=f"Slot in {panel_id} {gid}",
            ))

        # Use the first panel_id for the view (works since CancelSlotSelectView
        # looks up the panel from the reg doc itself)
        view = CancelSlotSelectView(regs[0]["panel_id"], options)
        await interaction.response.send_message(
            "❌ You have **multiple active slots**. Select which to cancel:",
            view=view,
            ephemeral=True,
        )

    @ui.button(
        label="🔄 Transfer Slot",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:transfer_slot",
    )
    async def transfer_slot_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        # Admin-only gate
        from bot.views.persistent import _check_admin
        if not await _check_admin(interaction):
            return

        guild_id = interaction.guild_id

        # Find any active registration to determine the panel
        reg = await registrations_col().find_one({
            "guild_id": guild_id,
            "status": {"$in": ["pending", "completed"]},
        })

        if not reg:
            await interaction.response.send_message(
                "❌ No active registrations found in this panel.",
                ephemeral=True,
            )
            return

        from bot.views.modals import TransferSlotModal
        await interaction.response.send_modal(
            TransferSlotModal(reg["panel_id"], guild_id)
        )

