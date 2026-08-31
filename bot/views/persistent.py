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


# ── Shared Admin Check ─────────────────────────────────────────────────────

async def _check_admin(interaction: discord.Interaction) -> bool:
    """Return True if the member is an Admin. Send ephemeral error and return False otherwise."""
    if interaction.user.guild_permissions.administrator:
        return True
    # Check member roles for any role named "Admin" (case-insensitive fallback)
    for role in interaction.user.roles:
        if role.name.lower() == "admin":
            return True
    await interaction.response.send_message(
        "❌ **Permission Denied** — This action is restricted to server Admins. "
        "If you believe this is an error, contact a server administrator.",
        ephemeral=True,
    )
    return False


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
            "group_id": "G01",
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
        if not await _check_admin(interaction):
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
        if not await _check_admin(interaction):
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
        if not await _check_admin(interaction):
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

        # Row 3: Group & Slot Management
        btn_manage_groups = ui.Button(label="🗂️ Manage Groups", style=discord.ButtonStyle.primary, custom_id=f"acp_mng_grps_{panel_id}", row=3)
        btn_assign_reserved = ui.Button(label="🛡️ Assign Reserved", style=discord.ButtonStyle.success, custom_id=f"acp_assign_res_{panel_id}", row=3)

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
        btn_manage_groups.callback = self._on_manage_groups
        btn_assign_reserved.callback = self._on_assign_reserved

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
        self.add_item(btn_manage_groups)
        self.add_item(btn_assign_reserved)

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
        from bot.views.modals import GroupScheduleModal
        await interaction.response.send_modal(GroupScheduleModal(self.panel_id, interaction.guild_id))

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

    async def _on_manage_groups(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        panel = await panels_col().find_one({"guild_id": interaction.guild_id, "panel_id": self.panel_id})
        if not panel:
            return await interaction.response.send_message("❌ Panel not found.", ephemeral=True)

        schedules = panel.get("schedules", [])
        if not schedules:
            return await interaction.response.send_message("❌ No groups configured.", ephemeral=True)

        options = []
        for s in schedules:
            gid = s.get("group_id", "G01")
            status = s.get("status", "open")
            status_icon = "🟢" if status == "open" else "🔴"
            cap = s.get("capacity", 20)
            options.append(discord.SelectOption(
                label=f"{status_icon} {gid} ({status.upper()})",
                value=gid,
                description=f"Capacity: {cap} | M1: {s.get('m1_time', 'TBD')} | M2: {s.get('m2_time', 'TBD')}",
            ))

        view = ManageGroupsView(self.bot, self.panel_id, options)
        await interaction.response.send_message(
            "🗂️ **Manage Groups** — Select groups to close or delete:\n"
            "• **Close** = hide from registration, keep data\n"
            "• **Delete** = remove channels, roles, and all data permanently",
            view=view,
            ephemeral=True,
        )

    async def _on_assign_reserved(self, interaction: discord.Interaction) -> None:
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)
        from bot.views.modals import AssignReservedSlotModal
        await interaction.response.send_modal(
            AssignReservedSlotModal(self.panel_id, interaction.guild_id)
        )


# ── Manage Groups View (ephemeral, admin-only) ─────────────────────────────

class ManageGroupsView(ui.View):
    """Admin view with a multi-select dropdown for groups + Close/Delete action buttons."""

    def __init__(self, bot, panel_id: str, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=120)
        self.bot = bot
        self.panel_id = panel_id
        self.selected_groups: list[str] = []

        select = ui.Select(
            placeholder="Select groups to manage…",
            options=options,
            min_values=1,
            max_values=len(options),
            custom_id="manage_groups_select",
        )
        select.callback = self._on_select
        self.add_item(select)

        btn_close = ui.Button(label="🔴 Close Selected", style=discord.ButtonStyle.secondary, custom_id="mng_grp_close", row=1)
        btn_reopen = ui.Button(label="🟢 Reopen Selected", style=discord.ButtonStyle.success, custom_id="mng_grp_reopen", row=1)
        btn_delete = ui.Button(label="🗑️ Delete Selected", style=discord.ButtonStyle.danger, custom_id="mng_grp_delete", row=1)

        btn_close.callback = self._on_close
        btn_reopen.callback = self._on_reopen
        btn_delete.callback = self._on_delete

        self.add_item(btn_close)
        self.add_item(btn_reopen)
        self.add_item(btn_delete)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        self.selected_groups = interaction.data.get("values", [])
        await interaction.response.send_message(
            f"✅ Selected: **{', '.join(self.selected_groups)}**. Now click an action button.",
            ephemeral=True,
        )

    async def _on_close(self, interaction: discord.Interaction) -> None:
        if not self.selected_groups:
            return await interaction.response.send_message("❌ Select groups first.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        cog = self.bot.get_cog("Panel")
        if cog and hasattr(cog, "close_specific_groups"):
            result = await cog.close_specific_groups(interaction.guild, self.panel_id, self.selected_groups)
            await interaction.followup.send(result, ephemeral=True)
        else:
            # Fallback: update directly
            panel = await panels_col().find_one({"guild_id": interaction.guild_id, "panel_id": self.panel_id})
            if panel:
                schedules = panel.get("schedules", [])
                for s in schedules:
                    if s.get("group_id") in self.selected_groups:
                        s["status"] = "closed"
                await panels_col().update_one(
                    {"guild_id": interaction.guild_id, "panel_id": self.panel_id},
                    {"$set": {"schedules": schedules}},
                )
            await interaction.followup.send(
                f"🔴 Groups **{', '.join(self.selected_groups)}** have been **closed**.\n"
                f"They will no longer appear in the registration portal.",
                ephemeral=True,
            )

    async def _on_reopen(self, interaction: discord.Interaction) -> None:
        if not self.selected_groups:
            return await interaction.response.send_message("❌ Select groups first.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        panel = await panels_col().find_one({"guild_id": interaction.guild_id, "panel_id": self.panel_id})
        if panel:
            schedules = panel.get("schedules", [])
            reopened = []
            for s in schedules:
                if s.get("group_id") in self.selected_groups and s.get("status") == "closed":
                    s["status"] = "open"
                    reopened.append(s["group_id"])
            await panels_col().update_one(
                {"guild_id": interaction.guild_id, "panel_id": self.panel_id},
                {"$set": {"schedules": schedules}},
            )
            if reopened:
                await interaction.followup.send(
                    f"🟢 Groups **{', '.join(reopened)}** have been **reopened**.\n"
                    f"Click **🚀 Post to Reg Portal** to update the registration embed.",
                    ephemeral=True,
                )
            else:
                await interaction.followup.send(
                    "ℹ️ No closed groups found among the selected groups.",
                    ephemeral=True,
                )
        else:
            await interaction.followup.send("❌ Panel not found.", ephemeral=True)

    async def _on_delete(self, interaction: discord.Interaction) -> None:
        if not self.selected_groups:
            return await interaction.response.send_message("❌ Select groups first.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)
        cog = self.bot.get_cog("Panel")
        if cog and hasattr(cog, "delete_specific_groups"):
            result = await cog.delete_specific_groups(interaction.guild, self.panel_id, self.selected_groups)
            await interaction.followup.send(result, ephemeral=True)
        else:
            await interaction.followup.send("❌ Delete handler not available.", ephemeral=True)

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
        """Choose Lobby — allows selecting a lobby or switching from current lobby."""
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        panel = await panels_col().find_one({
            "guild_id": guild_id, "panel_id": self.panel_id,
        })
        if not panel:
            return await interaction.response.send_message("❌ Panel not found.", ephemeral=True)

        window = panel.get("window", "8PM")

        # Find user's current registrations in this panel/window
        existing_regs = await registrations_col().find({
            "guild_id": guild_id,
            "panel_id": self.panel_id,
            "window": window,
            "claimer_discord_id": user_id,
            "status": {"$in": ["pending", "completed"]},
        }).to_list(25)
        user_groups = {r.get("group_id") for r in existing_regs}

        # Show group selection dropdown
        schedules = panel.get("schedules", [])
        open_schedules = [s for s in schedules if s.get("status", "open") == "open"]
        if not open_schedules:
            return await interaction.response.send_message("❌ No open groups available for this panel.", ephemeral=True)

        options = []
        for s in open_schedules:
            gid = s.get("group_id", "G01")
            cap = s.get("capacity", 20)
            filled = await registrations_col().count_documents({
                "guild_id": guild_id,
                "panel_id": self.panel_id,
                "window": window,
                "group_id": gid,
                "status": {"$in": ["pending", "completed"]},
            })
            is_current = gid in user_groups
            status_text = "📍 CURRENT" if is_current else ("FULL" if filled >= cap else f"{filled}/{cap}")
            desc = "Your current lobby" if is_current else f"M1: {s.get('m1_time', 'TBD')} | M2: {s.get('m2_time', 'TBD')}"
            options.append(discord.SelectOption(
                label=f"Lobby {gid} ({status_text})",
                value=gid,
                description=desc,
                emoji="📍" if is_current else ("🔴" if filled >= cap else "🟢"),
            ))

        view = ChooseLobbySelectView(self.panel_id, options)
        current_msg = f" (Currently in **{', '.join(user_groups)}**)" if user_groups else ""
        await interaction.response.send_message(
            f"🔀 **Select a lobby** to switch to or register for{current_msg}:",
            view=view,
            ephemeral=True,
        )

    async def _on_cancel(self, interaction: discord.Interaction) -> None:
        """Cancel Slot — with lobby selection when player has multiple slots."""
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        # Find ALL active registrations for this user in this panel
        regs = await registrations_col().find({
            "guild_id": guild_id,
            "panel_id": self.panel_id,
            "claimer_discord_id": user_id,
            "status": {"$in": ["pending", "completed"]},
        }).to_list(25)

        if not regs:
            return await interaction.response.send_message(
                "❌ You do not have an active slot to cancel.", ephemeral=True,
            )

        if len(regs) == 1:
            # Single slot — cancel directly
            await _execute_cancel(interaction, regs[0], self.panel_id)
            return

        # Multiple slots — show selection dropdown
        options = []
        for r in regs:
            gid = r.get("group_id", "G01")
            team_name = r.get("team_name") or "(pending)"
            options.append(discord.SelectOption(
                label=f"{gid} — {team_name}",
                value=str(r["_id"]),
                description=f"Slot in {gid}",
            ))

        view = CancelSlotSelectView(self.panel_id, options)
        await interaction.response.send_message(
            "❌ You have slots in **multiple lobbies**. Select which to cancel:",
            view=view,
            ephemeral=True,
        )

    async def _on_transfer(self, interaction: discord.Interaction) -> None:
        """Transfer Slot — Admin only, opens modal for source team + destination."""
        if not await _check_admin(interaction):
            return
        from bot.views.modals import TransferSlotModal
        await interaction.response.send_modal(
            TransferSlotModal(self.panel_id, interaction.guild_id)
        )

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
        """Role Transfer — opens modal, verifies same-team membership."""
        from bot.views.modals import RoleTransferModal
        await interaction.response.send_modal(
            RoleTransferModal(self.panel_id, interaction.guild_id, interaction.user.id)
        )


# ── Multi-Group Register View (#T1-reg-8PM) ────────────────────────────────

class MultiGroupRegisterView(ui.View):
    """
    Public registration view with Register button for each group.
    """

    def __init__(self, panel_id: str, group_count: int = 1, open_schedules: list[dict] | None = None) -> None:
        super().__init__(timeout=None)
        self.panel_id = panel_id

        if open_schedules:
            # Use actual open schedules — only creates buttons for open groups
            for idx, s in enumerate(open_schedules[:20]):
                gid = s.get("group_id", f"G{idx+1:02d}")
                btn = ui.Button(
                    label=f"📥 Register {gid}",
                    style=discord.ButtonStyle.success if idx % 2 == 0 else discord.ButtonStyle.primary,
                    custom_id=f"reg_btn_{panel_id}_{gid}",
                )
                btn.callback = self._make_callback(gid)
                self.add_item(btn)
        else:
            # Fallback: sequential G01..GN (legacy behavior)
            for i in range(1, min(group_count + 1, 21)):
                gid = f"G{i:02d}"
                btn = ui.Button(
                    label=f"📥 Register {gid}",
                    style=discord.ButtonStyle.success if i % 2 != 0 else discord.ButtonStyle.primary,
                    custom_id=f"reg_btn_{panel_id}_{gid}",
                )
                btn.callback = self._make_callback(gid)
                self.add_item(btn)

        # Set Slot Reminder button (Tortuga / Mack style)
        remind_btn = ui.Button(
            label="⏰ Set Slot Reminder",
            style=discord.ButtonStyle.secondary,
            custom_id=f"reg_remind_{panel_id}",
        )
        remind_btn.callback = self._on_remind_click
        self.add_item(remind_btn)

    async def _on_remind_click(self, interaction: discord.Interaction) -> None:
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
            f"🔔 **Reminder Set!** You will be notified as soon as any slot opens up in **{self.panel_id}**.",
            ephemeral=True,
        )

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

            # Check if group is closed
            schedules = panel.get("schedules", [])
            grp_sched = next((s for s in schedules if s.get("group_id") == group_id), {})
            if grp_sched.get("status") == "closed":
                return await interaction.response.send_message(
                    f"❌ **{group_id}** is currently **closed** for registration.", ephemeral=True,
                )

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

            cap = grp_sched.get("capacity", panel.get("max_slots", 20))
            reserved_count = grp_sched.get("reserved_slots", panel.get("default_reserved_slots", 0))

            if filled >= cap:
                return await interaction.response.send_message(
                    f"❌ **{group_id}** is currently full ({filled}/{cap}). Click 🔔 Reminders in slot management to get notified upon cancellations!",
                    ephemeral=True,
                )

            # Compute next available slot number (skip reserved slots)
            existing_teams = await teams_col().find({
                "guild_id": guild_id,
                "panel_id": self.panel_id,
                "window": window,
                "group_id": group_id,
            }).to_list(cap)
            taken_slots = {t.get("slot_number") for t in existing_teams if t.get("slot_number")}
            
            pending_regs = await registrations_col().find({
                "guild_id": guild_id,
                "panel_id": self.panel_id,
                "window": window,
                "group_id": group_id,
                "status": "pending",
            }).to_list(cap)
            taken_slots.update({r.get("slot_number") for r in pending_regs if r.get("slot_number")})

            # Public slots start after reserved slots
            next_slot = None
            for s_num in range(reserved_count + 1, cap + 1):
                if s_num not in taken_slots:
                    next_slot = s_num
                    break

            if next_slot is None:
                return await interaction.response.send_message(
                    f"❌ No open public slots available in **{group_id}**.", ephemeral=True,
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

            # Create Registration document with computed slot number
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
                "slot_number": next_slot,
            })

            tag_ch_id = panel.get("channel_ids", {}).get("tag_channel_id")
            tag_mention = f"<#{tag_ch_id}>" if tag_ch_id else "the tag channel"

            await interaction.response.send_message(
                f"✅ Slot reserved for **{group_id}**! Post your team in {tag_mention} within **{timeout_min} minutes** to confirm.\n"
                f"Format: `TeamName @p1 @p2 @p3 @p4`",
                ephemeral=True,
            )

        return _callback


# ── Choose Lobby Select View (ephemeral, non-persistent) ────────────────────

class ChooseLobbySelectView(ui.View):
    """Dropdown for selecting a lobby when player uses Choose Lobby."""

    def __init__(self, panel_id: str, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=60)
        self.panel_id = panel_id
        select = ui.Select(
            placeholder="Select a lobby…",
            options=options,
            custom_id="choose_lobby_select",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        group_id = interaction.data["values"][0]
        guild_id = interaction.guild_id
        user_id = interaction.user.id

        panel = await panels_col().find_one({"guild_id": guild_id, "panel_id": self.panel_id})
        if not panel:
            return await interaction.response.send_message("❌ Panel not found.", ephemeral=True)

        window = panel.get("window", "8PM")
        schedules = panel.get("schedules", [])
        grp_sched = next((s for s in schedules if s.get("group_id") == group_id), {})

        # Check if group is closed
        if grp_sched.get("status") == "closed":
            return await interaction.response.send_message(
                f"❌ **{group_id}** is currently **closed** for registration.", ephemeral=True,
            )

        # Check if user already has an active slot in this exact group
        existing_in_group = await registrations_col().find_one({
            "guild_id": guild_id,
            "panel_id": self.panel_id,
            "window": window,
            "group_id": group_id,
            "claimer_discord_id": user_id,
            "status": {"$in": ["pending", "completed"]},
        })
        if existing_in_group:
            return await interaction.response.send_message(
                f"❌ You already have a slot in **{group_id}**! Please select a different lobby if you wish to switch.",
                ephemeral=True,
            )

        cap = grp_sched.get("capacity", panel.get("max_slots", 20))
        reserved_count = grp_sched.get("reserved_slots", panel.get("default_reserved_slots", 0))

        filled = await registrations_col().count_documents({
            "guild_id": guild_id,
            "panel_id": self.panel_id,
            "window": window,
            "group_id": group_id,
            "status": {"$in": ["pending", "completed"]},
        })

        if filled >= cap:
            return await interaction.response.send_message(
                f"❌ **{group_id}** is currently **full** ({filled}/{cap}).",
                ephemeral=True,
            )

        # Compute next available slot number (skip reserved slots)
        existing_teams = await teams_col().find({
            "guild_id": guild_id,
            "panel_id": self.panel_id,
            "window": window,
            "group_id": group_id,
        }).to_list(cap)
        taken_slots = {t.get("slot_number") for t in existing_teams if t.get("slot_number")}

        pending_regs = await registrations_col().find({
            "guild_id": guild_id,
            "panel_id": self.panel_id,
            "window": window,
            "group_id": group_id,
            "status": "pending",
        }).to_list(cap)
        taken_slots.update({r.get("slot_number") for r in pending_regs if r.get("slot_number")})

        next_slot = None
        for s_num in range(reserved_count + 1, cap + 1):
            if s_num not in taken_slots:
                next_slot = s_num
                break

        if next_slot is None:
            return await interaction.response.send_message(
                f"❌ No open public slots available in **{group_id}**.", ephemeral=True,
            )

        timeout_min = panel.get("claim_timeout_minutes", 5)
        new_slot_label = f"{group_id}-{next_slot:02d}"
        guild = interaction.guild
        ch_ids = panel.get("channel_ids", {})
        role_map = ch_ids.get("lobby_roles", {})

        # Check if user has an existing slot in ANOTHER group (Switch flow)
        old_reg = await registrations_col().find_one({
            "guild_id": guild_id,
            "panel_id": self.panel_id,
            "window": window,
            "claimer_discord_id": user_id,
            "status": {"$in": ["pending", "completed"]},
        })

        if old_reg:
            old_group = old_reg.get("group_id", "G01")

            # 1. Update team record if already registered
            old_team = await teams_col().find_one({
                "guild_id": guild_id,
                "panel_id": self.panel_id,
                "window": window,
                "group_id": old_group,
                "owner_discord_id": user_id,
            })
            if old_team:
                await teams_col().update_one(
                    {"_id": old_team["_id"]},
                    {"$set": {
                        "group_id": group_id,
                        "slot_number": next_slot,
                        "slot_label": new_slot_label,
                    }},
                )

            # 2. Update registration document
            await registrations_col().update_one(
                {"_id": old_reg["_id"]},
                {"$set": {
                    "group_id": group_id,
                    "slot_number": next_slot,
                    "slot_label": new_slot_label,
                }},
            )

            # 3. Swap IDP roles on the user
            if guild:
                member = guild.get_member(user_id)
                if member:
                    old_role_id = role_map.get(old_group)
                    if old_role_id:
                        old_r = guild.get_role(old_role_id)
                        if old_r:
                            try:
                                await member.remove_roles(old_r, reason=f"Switched lobby from {old_group}")
                            except discord.Forbidden:
                                pass
                    new_role_id = role_map.get(group_id)
                    if new_role_id:
                        new_r = guild.get_role(new_role_id)
                        if new_r:
                            try:
                                await member.add_roles(new_r, reason=f"Switched lobby to {group_id}")
                            except discord.Forbidden:
                                pass

            # 4. Notify waitlisted players for the freed slot in old_group
            from shared.database import reminders_col
            old_reminders = await reminders_col().find({
                "guild_id": guild_id, "panel_id": self.panel_id, "group_id": old_group,
            }).to_list(100)
            for rem in old_reminders:
                try:
                    u = guild.get_member(rem["user_id"])
                    if u:
                        await u.send(f"🔔 **Slot Available!** A slot just opened up in **{self.panel_id} ({old_group})**!")
                except Exception:
                    pass
            await reminders_col().delete_many({
                "guild_id": guild_id, "panel_id": self.panel_id, "group_id": old_group,
            })

            # 5. Refresh slotboard
            bot = interaction.client
            sb_cog = bot.get_cog("SlotBoard")
            if sb_cog and hasattr(sb_cog, "refresh_board"):
                await sb_cog.refresh_board(guild_id, self.panel_id)

            team_name = old_reg.get("team_name")
            new_role_id = role_map.get(group_id)
            role_mention = f"<@&{new_role_id}>" if new_role_id else f"**{group_id}**"
            if team_name:
                return await interaction.response.send_message(
                    f"✅ **Lobby Switched Successfully!**\n"
                    f"• Team: **{team_name}**\n"
                    f"• Moved: **{old_group}** ➡️ **{group_id}** (Slot **#{next_slot}** — `{new_slot_label}`)\n"
                    f"• IDP role updated to {role_mention}.",
                    ephemeral=True,
                )
            else:
                return await interaction.response.send_message(
                    f"✅ **Lobby Reservation Switched!**\n"
                    f"• Moved: **{old_group}** ➡️ **{group_id}** (Slot **#{next_slot}** — `{new_slot_label}`)\n"
                    f"• Post your team in the tag channel within **{timeout_min} minutes** to confirm.",
                    ephemeral=True,
                )

        # ── Fresh claim flow (User had no slot yet) ──────────────────────
        role_id = panel.get("role_id")
        if role_id and guild:
            role = guild.get_role(role_id)
            if role:
                try:
                    await interaction.user.add_roles(role, reason=f"Choose Lobby → {group_id}")
                except discord.Forbidden:
                    pass

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
            "slot_number": next_slot,
            "slot_label": new_slot_label,
        })

        tag_ch_id = panel.get("channel_ids", {}).get("tag_channel_id")
        tag_mention = f"<#{tag_ch_id}>" if tag_ch_id else "the tag channel"

        await interaction.response.send_message(
            f"✅ Slot **#{next_slot}** reserved for **{group_id}**! Post your team in {tag_mention} "
            f"within **{timeout_min} minutes** to confirm.\n"
            f"Format: `TeamName @p1 @p2 @p3 @p4`",
            ephemeral=True,
        )


# ── Cancel Slot Select View (ephemeral, non-persistent) ────────────────────

async def _execute_cancel(
    interaction: discord.Interaction,
    reg: dict,
    panel_id: str,
) -> None:
    """Shared cancellation logic for single-slot or selected-slot cancel."""
    guild_id = interaction.guild_id
    user_id = interaction.user.id
    group_id = reg.get("group_id", "G01")
    window = reg.get("window", "")

    panel = await panels_col().find_one({"guild_id": guild_id, "panel_id": panel_id})

    # Cancel-lock check
    if panel:
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

    # Delete registration + team
    await registrations_col().delete_one({"_id": reg["_id"]})
    await teams_col().delete_many({
        "guild_id": guild_id,
        "panel_id": panel_id,
        "window": window,
        "group_id": group_id,
        "owner_discord_id": user_id,
    })

    # Revoke group IDP role
    if panel:
        ch_ids = panel.get("channel_ids", {})
        role_map = ch_ids.get("lobby_roles", {})
        role_id = role_map.get(group_id)
        if role_id:
            role = interaction.guild.get_role(role_id)
            if role:
                try:
                    await interaction.user.remove_roles(role, reason="Slot cancelled")
                except discord.Forbidden:
                    pass

        # Revoke tag-access role if no other active registrations remain
        remaining = await registrations_col().count_documents({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "claimer_discord_id": user_id,
            "status": {"$in": ["pending", "completed"]},
        })
        if remaining == 0:
            tag_role_id = panel.get("role_id")
            if tag_role_id:
                tag_role = interaction.guild.get_role(tag_role_id)
                if tag_role:
                    try:
                        await interaction.user.remove_roles(tag_role, reason="All slots cancelled")
                    except discord.Forbidden:
                        pass

    # Notify reminder subscribers
    from shared.database import reminders_col
    reminders = await reminders_col().find({
        "guild_id": guild_id, "panel_id": panel_id, "group_id": group_id,
    }).to_list(100)

    for rem in reminders:
        try:
            user = interaction.guild.get_member(rem["user_id"])
            if user:
                await user.send(
                    f"🔔 **Slot Available!** A slot just opened up in "
                    f"**{panel_id} ({group_id})**. Go claim it now!"
                )
        except Exception:
            pass

    await reminders_col().delete_many({
        "guild_id": guild_id, "panel_id": panel_id, "group_id": group_id,
    })

    # Try to respond (may already be responded to by select callback)
    try:
        await interaction.response.send_message(
            f"✅ Your slot in **{panel_id} ({group_id})** has been cancelled.",
            ephemeral=True,
        )
    except discord.errors.InteractionResponded:
        await interaction.followup.send(
            f"✅ Your slot in **{panel_id} ({group_id})** has been cancelled.",
            ephemeral=True,
        )

    # Refresh slot board
    bot = interaction.client
    cog = bot.get_cog("SlotBoard")
    if cog and hasattr(cog, "refresh_board"):
        await cog.refresh_board(guild_id, panel_id)


class CancelSlotSelectView(ui.View):
    """Dropdown for selecting which lobby slot to cancel (multi-slot players)."""

    def __init__(self, panel_id: str, options: list[discord.SelectOption]) -> None:
        super().__init__(timeout=60)
        self.panel_id = panel_id
        select = ui.Select(
            placeholder="Select slot to cancel…",
            options=options,
            custom_id="cancel_slot_select",
        )
        select.callback = self._on_select
        self.add_item(select)

    async def _on_select(self, interaction: discord.Interaction) -> None:
        from bson import ObjectId
        reg_id = interaction.data["values"][0]

        try:
            query = {"_id": ObjectId(reg_id)}
        except Exception:
            query = {"_id": reg_id}

        reg = await registrations_col().find_one(query)
        if not reg:
            return await interaction.response.send_message(
                "❌ Registration not found — it may have already been cancelled.",
                ephemeral=True,
            )

        await _execute_cancel(interaction, reg, self.panel_id)
