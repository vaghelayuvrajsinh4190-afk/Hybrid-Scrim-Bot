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

        # Find the user's active registration
        reg = await registrations_col().find_one({
            "guild_id": guild_id,
            "claimer_discord_id": user_id,
            "status": {"$in": ["pending", "completed"]},
        })
        if reg is None:
            await interaction.response.send_message(
                "❌ You don't have a slot to cancel.", ephemeral=True,
            )
            return

        panel_id = reg["panel_id"]
        panel = await panels_col().find_one({
            "guild_id": guild_id, "panel_id": panel_id,
        })
        if panel is None:
            await interaction.response.send_message(
                "❌ Panel not found.", ephemeral=True,
            )
            return

        # ── Edge Case 3: Cancel-slot lock window ──────────────────────
        match_start = panel.get("match_start_time")
        cancel_lock = panel.get("cancel_lock_minutes", 60)

        if match_start:
            now = datetime.now(timezone.utc)
            time_until = (match_start - now).total_seconds() / 60
            if time_until <= cancel_lock:
                await interaction.response.send_message(
                    f"❌ Cancellations are locked — match starts in under "
                    f"**{cancel_lock} minutes**. Contact an admin.",
                    ephemeral=True,
                )
                return

        window = reg["window"]

        # Remove registration + team
        await registrations_col().delete_one({"_id": reg["_id"]})
        await teams_col().delete_many({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
            "owner_discord_id": user_id,
        })

        # Revoke tag-access role
        role_id = panel.get("role_id")
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                try:
                    await interaction.user.remove_roles(
                        role, reason="Slot cancelled",
                    )
                except discord.Forbidden:
                    pass

        await interaction.response.send_message(
            "✅ Your slot has been **cancelled** and is now open.",
            ephemeral=True,
        )

        # Trigger slot board refresh
        cog = interaction.client.get_cog("SlotBoard")
        if cog and hasattr(cog, "refresh_board"):
            await cog.refresh_board(guild_id, panel_id)

    @ui.button(
        label="🔄 Transfer Slot",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:transfer_slot",
    )
    async def transfer_slot_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        reg = await registrations_col().find_one({
            "guild_id": guild_id,
            "claimer_discord_id": user_id,
            "status": {"$in": ["pending", "completed"]},
        })
        if reg is None:
            await interaction.response.send_message(
                "❌ You don't have a slot to transfer.", ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "📝 To transfer your slot, mention the user you want to "
            "transfer to in this channel.\n"
            "Example: `@NewOwner`\n"
            "The transfer will be processed by an admin.",
            ephemeral=True,
        )
