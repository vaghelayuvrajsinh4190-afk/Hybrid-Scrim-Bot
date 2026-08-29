"""
Persistent View classes.

All views here use ``timeout=None`` and stable ``custom_id`` strings
so they survive bot restarts.  They are registered via
``bot.add_view()`` in ``on_ready``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import ui

from shared.database import (
    bans_col,
    panels_col,
    players_col,
    registrations_col,
    teams_col,
)
from bot.utils.checks import is_banned
from bot.views.modals import LinkIDModal

log = logging.getLogger(__name__)


# ── Link ID Button ─────────────────────────────────────────────────────────

class LinkIDView(ui.View):
    """Persistent button for linking / updating a BGMI ID."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(
        label="🔗 Link Your ID",
        style=discord.ButtonStyle.primary,
        custom_id="persistent:link_id",
    )
    async def link_id_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        modal = LinkIDModal()
        # Check if user already has a linked ID to personalize the modal title
        existing = await players_col().find_one({
            "discord_id": interaction.user.id,
            "guild_id": interaction.guild_id,
        })
        if existing and existing.get("bgmi_id"):
            modal.title = "Update Your BGMI ID"
        await interaction.response.send_modal(modal)


# ── Claim Slot Button ─────────────────────────────────────────────────────

class ClaimSlotView(ui.View):
    """Persistent button on the registration embed for each panel."""

    def __init__(self, panel_id: str | None = None) -> None:
        super().__init__(timeout=None)
        self._panel_id = panel_id

    @ui.button(
        label="🎯 Claim Slot",
        style=discord.ButtonStyle.success,
        custom_id="persistent:claim_slot",
    )
    async def claim_slot_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        guild_id = interaction.guild_id

        # Determine panel from the channel this button lives in
        panel = await panels_col().find_one({
            "guild_id": guild_id,
            "channel_ids.reg_channel_id": interaction.channel_id,
        })
        if panel is None:
            await interaction.response.send_message(
                "❌ Could not determine which panel this belongs to.",
                ephemeral=True,
            )
            return

        panel_id = panel["panel_id"]
        window = panel["window"]
        user_id = interaction.user.id

        # 1. Ban check
        if await is_banned(user_id, guild_id):
            await interaction.response.send_message(
                "❌ You are **banned** from scrims.", ephemeral=True,
            )
            return

        # 2. Already claimed this window?
        existing_claim = await registrations_col().find_one({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
            "claimer_discord_id": user_id,
            "status": {"$in": ["pending", "completed"]},
        })
        if existing_claim:
            await interaction.response.send_message(
                "❌ You already have a slot claimed in this window.",
                ephemeral=True,
            )
            return

        # 3. Capacity check
        filled = await registrations_col().count_documents({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
            "status": {"$in": ["pending", "completed"]},
        })
        max_slots = panel.get("max_slots", 20)
        if filled >= max_slots:
            await interaction.response.send_message(
                "❌ All slots are **full** for this window.", ephemeral=True,
            )
            return

        # 4. Grant temp tag-access role
        role_id = panel.get("role_id")
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                try:
                    await interaction.user.add_roles(role, reason="Claimed scrim slot")
                except discord.Forbidden:
                    log.warning("Cannot add role %s to %s", role_id, user_id)

        # 5. Create Registration doc with deadline
        timeout_min = panel.get("claim_timeout_minutes", 5)
        now = datetime.now(timezone.utc)
        deadline = now + timedelta(minutes=timeout_min)

        await registrations_col().insert_one({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
            "claimer_discord_id": user_id,
            "claimed_at": now,
            "claim_deadline": deadline,
            "status": "pending",
            "team_name": None,
        })

        # 6. Direct to tag channel
        tag_ch_id = panel.get("channel_ids", {}).get("tag_channel_id")
        tag_mention = f"<#{tag_ch_id}>" if tag_ch_id else "the tag channel"

        await interaction.response.send_message(
            f"✅ Slot claimed! Post your team in {tag_mention} "
            f"within **{timeout_min} minutes** or it will be released.",
            ephemeral=True,
        )


# ── Admin Action Buttons ──────────────────────────────────────────────────

class AdminActionsView(ui.View):
    """Admin buttons: Remove Team, Clear Registration, Confirm Tag."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @ui.button(
        label="🗑️ Remove Team",
        style=discord.ButtonStyle.danger,
        custom_id="persistent:admin_remove_team",
    )
    async def remove_team_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Admin only.", ephemeral=True,
            )
            return

        # We expect the team name in the embed the button is attached to
        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            team_name = None
            for field in embed.fields:
                if field.name and "team" in field.name.lower():
                    team_name = field.value
                    break
            if team_name:
                guild_id = interaction.guild_id
                # Full wipe — team + registration
                await teams_col().delete_many({
                    "guild_id": guild_id, "team_name": team_name,
                })
                await registrations_col().delete_many({
                    "guild_id": guild_id, "team_name": team_name,
                })
                await interaction.response.send_message(
                    f"✅ Team **{team_name}** removed completely.",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            "❌ Could not determine team to remove.", ephemeral=True,
        )

    @ui.button(
        label="🧹 Clear Registration",
        style=discord.ButtonStyle.secondary,
        custom_id="persistent:admin_clear_reg",
    )
    async def clear_reg_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Admin only.", ephemeral=True,
            )
            return

        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            team_name = None
            panel_id = None
            window = None
            for field in embed.fields:
                fname = (field.name or "").lower()
                if "team" in fname:
                    team_name = field.value
                elif "panel" in fname:
                    panel_id = field.value
                elif "window" in fname:
                    window = field.value

            if team_name and panel_id and window:
                await registrations_col().delete_many({
                    "guild_id": interaction.guild_id,
                    "panel_id": panel_id,
                    "window": window,
                    "team_name": team_name,
                })
                await teams_col().delete_many({
                    "guild_id": interaction.guild_id,
                    "panel_id": panel_id,
                    "window": window,
                    "team_name": team_name,
                })
                await interaction.response.send_message(
                    f"✅ Registration cleared for **{team_name}** "
                    f"(window: {window}). Team profile kept.",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            "❌ Could not determine registration to clear.", ephemeral=True,
        )

    @ui.button(
        label="✅ Confirm Tag & Give Slot",
        style=discord.ButtonStyle.primary,
        custom_id="persistent:admin_confirm_tag",
    )
    async def confirm_tag_btn(
        self, interaction: discord.Interaction, button: ui.Button,
    ) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Admin only.", ephemeral=True,
            )
            return

        if interaction.message and interaction.message.embeds:
            embed = interaction.message.embeds[0]
            team_name = None
            panel_id = None
            window = None
            for field in embed.fields:
                fname = (field.name or "").lower()
                if "team" in fname:
                    team_name = field.value
                elif "panel" in fname:
                    panel_id = field.value
                elif "window" in fname:
                    window = field.value

            if team_name and panel_id and window:
                guild_id = interaction.guild_id
                # Mark registration as completed
                await registrations_col().update_one(
                    {
                        "guild_id": guild_id,
                        "panel_id": panel_id,
                        "window": window,
                        "team_name": team_name,
                    },
                    {"$set": {"status": "completed"}},
                )
                # Mark team as confirmed
                await teams_col().update_one(
                    {
                        "guild_id": guild_id,
                        "panel_id": panel_id,
                        "window": window,
                        "team_name": team_name,
                    },
                    {"$set": {"confirmed": True}},
                )
                await interaction.response.send_message(
                    f"✅ Team **{team_name}** confirmed and slot granted.",
                    ephemeral=True,
                )
                return

        await interaction.response.send_message(
            "❌ Could not determine team to confirm.", ephemeral=True,
        )


# ── Panel Control View (Schedule Registration) ───────────────────────────

class PanelControlView(ui.View):
    """
    Persistent admin-control view posted to the slotmng (admin panel)
    channel.  Contains the Schedule Registration button.

    Because ``custom_id`` must be known at startup for persistence, this
    view uses a *dynamic* ``custom_id`` containing the ``panel_id``.
    A separate instance is registered for each panel in ``on_ready``.
    """

    def __init__(self, bot: commands.Bot, panel_id: str) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self._panel_id = panel_id

        # Dynamically-created button with stable, panel-specific custom_id
        btn = ui.Button(
            label="⏰ Schedule Registration",
            style=discord.ButtonStyle.primary,
            custom_id=f"schedule_btn_{panel_id}",
        )
        btn.callback = self._schedule_callback
        self.add_item(btn)

    async def _schedule_callback(
        self, interaction: discord.Interaction,
    ) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message(
                "❌ Admin only.", ephemeral=True,
            )
            return

        panel = await panels_col().find_one({
            "guild_id": interaction.guild_id,
            "panel_id": self._panel_id,
        })
        if panel is None:
            await interaction.response.send_message(
                "❌ Panel not found.", ephemeral=True,
            )
            return

        reg_channel_id = (panel.get("channel_ids") or {}).get("reg_channel_id")
        if not reg_channel_id:
            await interaction.response.send_message(
                "❌ No registration channel configured for this panel.",
                ephemeral=True,
            )
            return

        from bot.views.modals import ScheduleModal

        modal = ScheduleModal(
            bot=self.bot,
            panel_id=self._panel_id,
            channel_id=reg_channel_id,
            guild_id=interaction.guild_id,
        )
        await interaction.response.send_modal(modal)

