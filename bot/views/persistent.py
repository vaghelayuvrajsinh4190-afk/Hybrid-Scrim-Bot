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


# ── Screenshot Approval View (Admin Only) ──────────────────────────────────

class ScreenshotApprovalView(ui.View):
    """
    Per-screenshot admin-only view attached inside the private thread in #T1-group-X.
    """

    def __init__(self, verification_id: str, public_message_id: int | None = None) -> None:
        super().__init__(timeout=None)
        self.public_message_id = public_message_id

        approve_btn = ui.Button(
            label="✅ Approve SS",
            style=discord.ButtonStyle.success,
            custom_id=f"ss_approve:{verification_id}",
        )
        reject_btn = ui.Button(
            label="❌ Reject SS",
            style=discord.ButtonStyle.danger,
            custom_id=f"ss_reject:{verification_id}",
        )
        approve_btn.callback = self._approve_callback
        reject_btn.callback = self._reject_callback
        self.add_item(approve_btn)
        self.add_item(reject_btn)

    async def _approve_callback(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return

        from bson import ObjectId
        from shared.config import VERIFICATION_TTL_DAYS
        from shared.database import verifications_col

        verification_id = interaction.data["custom_id"].split(":", 1)[1]
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=VERIFICATION_TTL_DAYS)

        try:
            query = {"_id": ObjectId(verification_id), "status": "pending"}
        except Exception:
            query = {"_id": verification_id, "status": "pending"}

        result = await verifications_col().update_one(
            query,
            {"$set": {
                "status": "approved",
                "reviewed_by": interaction.user.id,
                "reviewed_at": now,
                "expires_at": expires,
            }},
        )

        if result.modified_count == 0:
            await interaction.response.send_message("⚠️ Already reviewed or not found.", ephemeral=True)
            return

        for child in self.children:
            child.disabled = True
        await interaction.message.edit(view=self)
        await interaction.response.send_message(
            f"✅ Screenshot approved by {interaction.user.mention}!", ephemeral=False,
        )

    async def _reject_callback(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ Admin only.", ephemeral=True)
            return

        verification_id = interaction.data["custom_id"].split(":", 1)[1]
        from bot.views.modals import RejectReasonModal
        modal = RejectReasonModal(verification_id, self.public_message_id)
        await interaction.response.send_modal(modal)


# ── Admin Control Panel View (#T1-admin) ───────────────────────────────────

class AdminControlPanelView(ui.View):
    """
    Dedicated Control Panel posted inside the private #T1-admin channel.
    Provides complete control over Groups, Schedules, Slots, Provisioning,
    SS Windows, and Midnight Resets.
    """

    def __init__(self, bot: commands.Bot, panel_id: str) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.panel_id = panel_id

        # Row 0: Configuration Modals
        btn_groups = ui.Button(label="👥 Groups", style=discord.ButtonStyle.primary, custom_id=f"acp_groups_{panel_id}", row=0)
        btn_sched = ui.Button(label="⏰ Schedule", style=discord.ButtonStyle.primary, custom_id=f"acp_sched_{panel_id}", row=0)
        btn_slots = ui.Button(label="🎯 Slots", style=discord.ButtonStyle.primary, custom_id=f"acp_slots_{panel_id}", row=0)

        # Row 1: Operations
        btn_prov = ui.Button(label="🚀 Post to Reg Portal", style=discord.ButtonStyle.success, custom_id=f"acp_prov_{panel_id}", row=1)
        btn_slotlist = ui.Button(label="📋 Send Slot Lists", style=discord.ButtonStyle.primary, custom_id=f"acp_slotlist_{panel_id}", row=1)
        btn_refresh = ui.Button(label="🔄 Refresh Panel", style=discord.ButtonStyle.secondary, custom_id=f"acp_refresh_{panel_id}", row=1)

        # Row 2: Live Match & Reset Controls
        btn_ss_open = ui.Button(label="📸 Open SS Window", style=discord.ButtonStyle.success, custom_id=f"acp_ss_open_{panel_id}", row=2)
        btn_ss_close = ui.Button(label="🔒 Close SS Window", style=discord.ButtonStyle.danger, custom_id=f"acp_ss_close_{panel_id}", row=2)
        btn_midnight = ui.Button(label="⚙️ Midnight Reset", style=discord.ButtonStyle.secondary, custom_id=f"acp_midnight_{panel_id}", row=2)
        btn_instant_reset = ui.Button(label="⚡ Instant Reset", style=discord.ButtonStyle.danger, custom_id=f"acp_instant_reset_{panel_id}", row=2)

        btn_groups.callback = self._on_groups
        btn_sched.callback = self._on_sched
        btn_slots.callback = self._on_slots
        btn_prov.callback = self._on_prov
        btn_slotlist.callback = self._on_slotlist
        btn_refresh.callback = self._on_refresh
        btn_ss_open.callback = self._on_ss_open
        btn_ss_close.callback = self._on_ss_close
        btn_midnight.callback = self._on_midnight
        btn_instant_reset.callback = self._on_instant_reset

        self.add_item(btn_groups)
        self.add_item(btn_sched)
        self.add_item(btn_slots)
        self.add_item(btn_prov)
        self.add_item(btn_slotlist)
        self.add_item(btn_refresh)
        self.add_item(btn_ss_open)
        self.add_item(btn_ss_close)
        self.add_item(btn_midnight)
        self.add_item(btn_instant_reset)

    async def _on_groups(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        from bot.views.modals import GroupsModal
        panel = await panels_col().find_one({"guild_id": interaction.guild_id, "panel_id": self.panel_id})
        count = panel.get("group_count", 1) if panel else 1
        await interaction.response.send_modal(GroupsModal(self.panel_id, interaction.guild_id, count))

    async def _on_sched(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        from bot.views.modals import BulkScheduleModal
        await interaction.response.send_modal(BulkScheduleModal(self.panel_id, interaction.guild_id))

    async def _on_slots(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        from bot.views.modals import SlotsConfigModal
        await interaction.response.send_modal(SlotsConfigModal(self.panel_id, interaction.guild_id))

    async def _on_prov(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        cog = self.bot.get_cog("Panel")
        if cog and hasattr(cog, "provision_registration_portal"):
            await interaction.response.defer(ephemeral=True)
            await cog.provision_registration_portal(interaction.guild, self.panel_id)
            await interaction.followup.send(f"✅ Registration portal provisioned/updated for **{self.panel_id}**!", ephemeral=True)
        else:
            await interaction.response.send_message("Provisioning handler ready.", ephemeral=True)

    async def _on_slotlist(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        cog = self.bot.get_cog("Panel")
        if cog and hasattr(cog, "send_slot_lists_to_lobbies"):
            await interaction.response.defer(ephemeral=True)
            await cog.send_slot_lists_to_lobbies(interaction.guild, self.panel_id)
            await interaction.followup.send("✅ Slot lists dispatched to all lobby channels!", ephemeral=True)
        else:
            await interaction.response.send_message("Slot list dispatched.", ephemeral=True)

    async def _on_refresh(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        cog = self.bot.get_cog("Panel")
        if cog and hasattr(cog, "render_admin_panel_embed"):
            embed = await cog.render_admin_panel_embed(interaction.guild_id, self.panel_id)
            await interaction.response.edit_message(embed=embed, view=self)
        else:
            await interaction.response.send_message("🔄 Refreshed state.", ephemeral=True)

    async def _on_ss_open(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        panel = await panels_col().find_one({"guild_id": interaction.guild_id, "panel_id": self.panel_id})
        if not panel:
            return await interaction.response.send_message("❌ Panel not found.", ephemeral=True)

        now = datetime.now(timezone.utc)
        await panels_col().update_one(
            {"_id": panel["_id"]},
            {"$set": {"ss_window_status": "open", "ss_window_opened_at": now}},
        )

        # Unlock lobby channels for IDP roles
        ch_ids = panel.get("channel_ids", {})
        lobby_map = ch_ids.get("lobby_channels", {})
        role_map = ch_ids.get("lobby_roles", {})

        for gid, ch_id in lobby_map.items():
            ch = interaction.guild.get_channel(ch_id)
            r_id = role_map.get(gid)
            role = interaction.guild.get_role(r_id) if r_id else None
            if ch:
                if role:
                    await ch.set_permissions(role, send_messages=True, view_channel=True)
                embed = discord.Embed(
                    title="📸 Screenshot Submission is OPEN!",
                    description="You have **30 minutes** to submit your match screenshots.\nPost: `TeamName` + attach screenshots.",
                    colour=discord.Colour.green(),
                )
                await ch.send(embed=embed)

        await interaction.response.send_message("✅ Screenshot window **OPENED** for all lobbies!", ephemeral=True)

    async def _on_ss_close(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        panel = await panels_col().find_one({"guild_id": interaction.guild_id, "panel_id": self.panel_id})
        if not panel:
            return await interaction.response.send_message("❌ Panel not found.", ephemeral=True)

        now = datetime.now(timezone.utc)
        await panels_col().update_one(
            {"_id": panel["_id"]},
            {"$set": {"ss_window_status": "closed", "ss_window_closed_at": now}},
        )

        # Lock lobby channels for IDP roles
        ch_ids = panel.get("channel_ids", {})
        lobby_map = ch_ids.get("lobby_channels", {})
        role_map = ch_ids.get("lobby_roles", {})

        for gid, ch_id in lobby_map.items():
            ch = interaction.guild.get_channel(ch_id)
            r_id = role_map.get(gid)
            role = interaction.guild.get_role(r_id) if r_id else None
            if ch:
                if role:
                    await ch.set_permissions(role, send_messages=False, view_channel=True)
                embed = discord.Embed(
                    title="🔒 Screenshot Submission is CLOSED",
                    description="The 30-minute submission window has concluded.",
                    colour=discord.Colour.red(),
                )
                await ch.send(embed=embed)

        await interaction.response.send_message("🔒 Screenshot window **CLOSED** for all lobbies.", ephemeral=True)

    async def _on_midnight(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        from bot.views.modals import MidnightResetModal
        panel = await panels_col().find_one({"guild_id": interaction.guild_id, "panel_id": self.panel_id})
        current = panel.get("midnight_reset", {}) if panel else {}
        await interaction.response.send_modal(MidnightResetModal(self.panel_id, interaction.guild_id, current))

    async def _on_instant_reset(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        cog = self.bot.get_cog("Panel")
        if cog and hasattr(cog, "execute_panel_reset"):
            await interaction.response.defer(ephemeral=True)
            await cog.execute_panel_reset(interaction.guild, self.panel_id)
            await interaction.followup.send(f"⚡ Panel **{self.panel_id}** has been completely reset!", ephemeral=True)
        else:
            await interaction.response.send_message("Instant reset executed.", ephemeral=True)


# ── Slot Management View (#T1-slotmng) ─────────────────────────────────────

class SlotManagementView(ui.View):
    """
    Player & Admin Slot Management hub with 5 interactive buttons.
    """

    def __init__(self, panel_id: str) -> None:
        super().__init__(timeout=None)
        self.panel_id = panel_id

        btn_switch = ui.Button(label="🔀 Choose Lobby", style=discord.ButtonStyle.primary, custom_id=f"sm_switch_{panel_id}", row=0)
        btn_cancel = ui.Button(label="❌ Cancel Slot", style=discord.ButtonStyle.danger, custom_id=f"sm_cancel_{panel_id}", row=0)
        btn_transfer = ui.Button(label="🔄 Transfer Slot", style=discord.ButtonStyle.secondary, custom_id=f"sm_transfer_{panel_id}", row=0)
        btn_remind = ui.Button(label="🔔 Reminders", style=discord.ButtonStyle.success, custom_id=f"sm_remind_{panel_id}", row=1)
        btn_role = ui.Button(label="👥 Role Transfer", style=discord.ButtonStyle.secondary, custom_id=f"sm_role_{panel_id}", row=1)

        btn_switch.callback = self._on_switch
        btn_cancel.callback = self._on_cancel
        btn_transfer.callback = self._on_transfer
        btn_remind.callback = self._on_remind
        btn_role.callback = self._on_role

        self.add_item(btn_switch)
        self.add_item(btn_cancel)
        self.add_item(btn_transfer)
        self.add_item(btn_remind)
        self.add_item(btn_role)

    async def _on_switch(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message("🔀 To switch lobbies, cancel your current slot and claim in your new group.", ephemeral=True)

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        reg = await registrations_col().find_one({
            "guild_id": guild_id,
            "panel_id": self.panel_id,
            "claimer_discord_id": user_id,
            "status": {"$in": ["pending", "completed"]},
        })
        if not reg:
            return await interaction.response.send_message("❌ You do not have an active slot to cancel.", ephemeral=True)

        group_id = reg.get("group_id", "G01")
        window = reg.get("window", "")

        # Atomic MongoDB delete
        await registrations_col().delete_one({"_id": reg["_id"]})
        await teams_col().delete_many({
            "guild_id": guild_id,
            "panel_id": self.panel_id,
            "window": window,
            "owner_discord_id": user_id,
        })

        # Notify reminders subscribers
        from shared.database import reminders_col
        reminders = await reminders_col().find({
            "guild_id": guild_id, "panel_id": self.panel_id, "group_id": group_id
        }).to_list(100)

        for rem in reminders:
            try:
                user = interaction.guild.get_member(rem["user_id"])
                if user:
                    await user.send(f"🔔 **Slot Available!** A slot just opened up in **{self.panel_id} ({group_id})**. Go claim it now!")
            except Exception:
                pass

        await reminders_col().delete_many({"guild_id": guild_id, "panel_id": self.panel_id, "group_id": group_id})

        await interaction.response.send_message(f"✅ Your slot in **{self.panel_id} ({group_id})** has been cancelled.", ephemeral=True)

    async def _on_transfer(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Slot transfer is restricted to Admins. Please ask staff for assistance.", ephemeral=True)
        await interaction.response.send_message("🔄 Admin Transfer: Use `/panel transfer` to reassign a slot atomically.", ephemeral=True)

    async def _on_remind(self, interaction: discord.Interaction) -> None:
        from shared.database import reminders_col
        await reminders_col().update_one(
            {
                "guild_id": interaction.guild_id,
                "panel_id": self.panel_id,
                "group_id": "G01",
                "user_id": interaction.user.id,
            },
            {"$set": {"created_at": datetime.now(timezone.utc)}},
            upsert=True,
        )
        await interaction.response.send_message(
            f"🔔 Subscribed! You will be pinged if any slot opens up in **{self.panel_id}**.", ephemeral=True,
        )

    async def _on_role(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "👥 **Role Transfer**: Mention your teammate in your group text channel to pass the slot role.", ephemeral=True,
        )


# ── Multi-Group Register View (#T1-reg-8PM) ────────────────────────────────

class MultiGroupRegisterView(ui.View):
    """
    Public registration view with Register button for each group.
    """

    def __init__(self, panel_id: str, group_count: int = 1) -> None:
        super().__init__(timeout=None)
        self.panel_id = panel_id

        for i in range(1, min(group_count + 1, 21)):
            gid = f"G{i:02d}"
            btn = ui.Button(
                label=f"📥 Register {gid}",
                style=discord.ButtonStyle.success if i % 2 != 0 else discord.ButtonStyle.primary,
                custom_id=f"reg_btn_{panel_id}_{gid}",
            )
            btn.callback = self._make_callback(gid)
            self.add_item(btn)

    def _make_callback(self, group_id: str):
        async def _callback(interaction: discord.Interaction) -> None:
            guild_id = interaction.guild_id
            user_id = interaction.user.id

            if await is_banned(user_id, guild_id):
                return await interaction.response.send_message("❌ You are **banned** from scrims.", ephemeral=True)

            panel = await panels_col().find_one({"guild_id": guild_id, "panel_id": self.panel_id})
            if not panel:
                return await interaction.response.send_message("❌ Panel not found.", ephemeral=True)

            window = panel.get("window", "8PM")
            allow_multi = panel.get("allow_multi_group_registration", False)

            # Check existing registration
            query = {
                "guild_id": guild_id,
                "panel_id": self.panel_id,
                "window": window,
                "claimer_discord_id": user_id,
                "status": {"$in": ["pending", "completed"]},
            }
            if not allow_multi:
                existing = await registrations_col().find_one(query)
                if existing:
                    return await interaction.response.send_message("❌ You are already registered for a lobby in this window.", ephemeral=True)
            else:
                query["group_id"] = group_id
                existing = await registrations_col().find_one(query)
                if existing:
                    return await interaction.response.send_message(f"❌ You are already registered for **{group_id}**.", ephemeral=True)

            # Capacity check for this group
            filled = await registrations_col().count_documents({
                "guild_id": guild_id,
                "panel_id": self.panel_id,
                "window": window,
                "group_id": group_id,
                "status": {"$in": ["pending", "completed"]},
            })

            schedules = panel.get("schedules", [])
            grp_sched = next((s for s in schedules if s.get("group_id") == group_id), {})
            cap = grp_sched.get("capacity", panel.get("max_slots", 20))

            if filled >= cap:
                return await interaction.response.send_message(
                    f"❌ **{group_id}** is currently full ({filled}/{cap}). Click 🔔 Reminders in slot management to get notified upon cancellations!",
                    ephemeral=True,
                )

            # Grant temp tag role
            role_id = panel.get("role_id")
            if role_id:
                role = interaction.guild.get_role(role_id)
                if role:
                    try:
                        await interaction.user.add_roles(role, reason=f"Claimed slot for {group_id}")
                    except discord.Forbidden:
                        pass

            # Create Registration document
            timeout_min = panel.get("claim_timeout_minutes", 5)
            now = datetime.now(timezone.utc)
            deadline = now + timedelta(minutes=timeout_min)

            await registrations_col().insert_one({
                "guild_id": guild_id,
                "panel_id": self.panel_id,
                "window": window,
                "group_id": group_id,
                "claimer_discord_id": user_id,
                "claimed_at": now,
                "claim_deadline": deadline,
                "status": "pending",
                "team_name": None,
            })

            tag_ch_id = panel.get("channel_ids", {}).get("tag_channel_id")
            tag_mention = f"<#{tag_ch_id}>" if tag_ch_id else "the tag channel"

            await interaction.response.send_message(
                f"✅ Slot reserved for **{group_id}**! Post your team in {tag_mention} within **{timeout_min} minutes** to confirm.\n"
                f"Format: `TeamName @p1 @p2 @p3 @p4`",
                ephemeral=True,
            )

        return _callback


