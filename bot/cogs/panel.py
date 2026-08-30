"""
Multi-Group Panel Management Cog (Mack Bot Architecture).

Handles:
  - Panel creation & multi-group provisioning (#T1-admin, #T1-reg-8PM, #T1-slotmng, #T1-winner, #T1-group-X)
  - Admin Control Panel (ACP) interactive view and refresh
  - Slot lists & match reminders dispatch
  - Rate-limited team promotions (/promote_teams) & winner announcements
  - Manual & automatic midnight panel resets
"""

from __future__ import annotations

import asyncio
import io
import logging
from datetime import datetime, timezone
from typing import List, Optional

import discord
from discord import app_commands
from discord.ext import commands

from shared.config import (
    DEFAULT_CANCEL_LOCK_MINUTES,
    DEFAULT_CLAIM_TIMEOUT_MINUTES,
    DEFAULT_MAX_SLOTS,
    DEFAULT_SCREENSHOT_WINDOW_MINUTES,
)
from shared.database import panels_col, points_col, registrations_col, teams_col
from shared.models import ChannelIds, MidnightResetConfig, PanelConfig, PointsTable

from bot.utils.channel_ops import ensure_category, ensure_role, ensure_text_channel
from bot.utils.checks import admin_only
from bot.utils.cooldown import check_rename_allowed, record_rename
from bot.utils.progress_bar import generate_segmented_bar
from bot.views.modals import PanelRenameModal, PanelSettingsModal
from bot.views.persistent import (
    AdminControlPanelView,
    MultiGroupRegisterView,
    SlotManagementView,
)

log = logging.getLogger(__name__)


class PanelGroup(app_commands.Group):
    """Panel management commands."""
    def __init__(self) -> None:
        super().__init__(name="panel", description="Manage scrim panels")


class PanelCog(commands.Cog, name="Panel"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.panel_group = PanelGroup()
        self.bot.tree.add_command(self.panel_group)
        self._register_commands()

    # ── Helpers for ACP & Portal ──────────────────────────────────────────

    async def render_admin_panel_embed(self, guild_id: int, panel_id: str) -> discord.Embed:
        """Build the rich status embed for #T1-admin."""
        panel = await panels_col().find_one({"guild_id": guild_id, "panel_id": panel_id})
        if not panel:
            return discord.Embed(title=f"🛠️ Panel {panel_id} Not Found", colour=discord.Colour.red())

        upper = panel_id.upper()
        group_count = panel.get("group_count", 1)
        schedules = panel.get("schedules", [])
        status = panel.get("status", "pending")
        ss_status = panel.get("ss_window_status", "closed")
        midnight_cfg = panel.get("midnight_reset", {})

        embed = discord.Embed(
            title=f"⚙️ {upper} — Admin Control Panel",
            description=(
                f"**Window:** `{panel.get('window')}` | **Groups:** `{group_count}` | **Status:** `{status.upper()}`\n"
                f"**SS Window:** `{'🟢 OPEN (30m)' if ss_status == 'open' else '🔴 CLOSED'}` | **Midnight Reset:** `{'🟢 ON' if midnight_cfg.get('enabled', True) else '🔴 OFF'}`"
            ),
            colour=discord.Colour.blurple(),
            timestamp=datetime.now(timezone.utc),
        )

        sched_lines = []
        for s in schedules:
            gid = s.get("group_id", "G01")
            m1 = s.get("m1_time", "12:00 PM")
            m2 = s.get("m2_time", "12:45 PM")
            map1 = s.get("m1_map", "Erangel")
            map2 = s.get("m2_map", "Miramar")
            cap = s.get("capacity", 20)
            res = s.get("reserved_slots", 1)
            sched_lines.append(f"• **{gid}**: M1 `{m1}` ({map1}) | M2 `{m2}` ({map2}) | Slots: `{cap}` (Res: `{res}`)")

        embed.add_field(
            name="🎮 Lobbies & Schedule",
            value="\n".join(sched_lines) if sched_lines else "No groups configured.",
            inline=False,
        )

        embed.set_footer(text=f"Panel {upper} • Use buttons below to manage")
        return embed

    async def provision_registration_portal(self, guild: discord.Guild, panel_id: str) -> None:
        """Post/update the registration embeds in #T1-reg and slotmng channel."""
        panel = await panels_col().find_one({"guild_id": guild.id, "panel_id": panel_id})
        if not panel:
            return

        ch_ids = panel.get("channel_ids", {})
        reg_ch_id = ch_ids.get("reg_channel_id")
        slotmng_ch_id = ch_ids.get("slotmng_channel_id")

        upper = panel_id.upper()
        window = panel.get("window", "8PM")
        group_count = panel.get("group_count", 1)
        schedules = panel.get("schedules", [])

        # 1. Post/Update in #T1-reg-8PM
        if reg_ch_id:
            reg_ch = guild.get_channel(reg_ch_id)
            if reg_ch:
                reg_embed = discord.Embed(
                    title=f"🏆 {upper} Scrims Registration — {window}",
                    description=(
                        f"Select your group below to claim a slot!\n"
                        f"After clicking, submit your team in the tag channel.\n\n"
                        f"**Available Groups:** `{group_count}`"
                    ),
                    colour=discord.Colour.gold(),
                    timestamp=datetime.now(timezone.utc),
                )

                # Segmented progress bars for each group
                for s in schedules:
                    gid = s.get("group_id", "G01")
                    cap = s.get("capacity", 20)
                    filled = await registrations_col().count_documents({
                        "guild_id": guild.id,
                        "panel_id": panel_id,
                        "window": window,
                        "group_id": gid,
                        "status": {"$in": ["pending", "completed"]},
                    })
                    m1 = s.get("m1_time", "12:00 PM")
                    m2 = s.get("m2_time", "12:45 PM")
                    reg_embed.add_field(
                        name=f"🎮 Lobby {gid} ({filled}/{cap} Slots)",
                        value=f"• Match 1: `{m1}` ({s.get('m1_map', 'Erangel')})\n• Match 2: `{m2}` ({s.get('m2_map', 'Miramar')})",
                        inline=False,
                    )

                # Offload progress bar image to worker thread via asyncio.to_thread
                first_cap = schedules[0].get("capacity", 20) if schedules else 20
                first_filled = await registrations_col().count_documents({
                    "guild_id": guild.id, "panel_id": panel_id, "window": window, "status": {"$in": ["pending", "completed"]}
                })
                progress_file = await generate_segmented_bar(first_filled, first_cap * max(1, group_count))
                reg_embed.set_image(url=f"attachment://{progress_file.filename}")

                view = MultiGroupRegisterView(panel_id=panel_id, group_count=group_count)
                msg = await reg_ch.send(embed=reg_embed, file=progress_file, view=view)
                await panels_col().update_one(
                    {"_id": panel["_id"]},
                    {"$set": {"reg_message_id": msg.id}},
                )

        # 2. Post/Update in #T1-slotmng (Slot Board + Management Hub)
        if slotmng_ch_id:
            slotmng_ch = guild.get_channel(slotmng_ch_id)
            if slotmng_ch:
                # 2a. Post the live Slot Board first
                cog = self.bot.get_cog("SlotBoard")
                if cog and hasattr(cog, "refresh_board"):
                    await cog.refresh_board(guild.id, panel_id)

                # 2b. Post the Slot Management hub buttons below
                sm_embed = discord.Embed(
                    title=f"🎯 {upper} — Slot Management Hub",
                    description=(
                        "Manage your scrim slot, switch lobbies, or subscribe to reminders.\n\n"
                        "• **🔀 Choose Lobby:** Switch your team to an open group\n"
                        "• **❌ Cancel Slot:** Free your slot and notify waitlisted players\n"
                        "• **🔄 Transfer Slot:** Admin re-assignment\n"
                        "• **🔔 Reminders:** Get notified as soon as a slot opens\n"
                        "• **👥 Role Transfer:** Pass your slot role to a teammate"
                    ),
                    colour=discord.Colour.blurple(),
                )
                sm_embed.set_footer(text=f"Panel {upper}")
                await slotmng_ch.send(embed=sm_embed, view=SlotManagementView(panel_id=panel_id))

    async def send_slot_lists_to_lobbies(self, guild: discord.Guild, panel_id: str) -> None:
        """Send formatted match reminders and slot list embeds into each lobby channel."""
        panel = await panels_col().find_one({"guild_id": guild.id, "panel_id": panel_id})
        if not panel:
            return

        ch_ids = panel.get("channel_ids", {})
        lobby_map = ch_ids.get("lobby_channels", {})
        window = panel.get("window", "8PM")
        schedules = panel.get("schedules", [])

        for gid, ch_id in lobby_map.items():
            ch = guild.get_channel(ch_id)
            if not isinstance(ch, discord.TextChannel):
                continue

            sched = next((s for s in schedules if s.get("group_id") == gid), {})
            m1_time = sched.get("m1_time", "TBD")
            m2_time = sched.get("m2_time", "TBD")
            m1_map = sched.get("m1_map", "Erangel")
            m2_map = sched.get("m2_map", "Miramar")
            cap = sched.get("capacity", 20)
            res_count = sched.get("reserved_slots", 1)

            # 1. Match Reminder Embed
            reminder_embed = discord.Embed(
                title=f"🚨 MATCH SCHEDULE & RULES — {panel_id.upper()} ({gid})",
                description=(
                    f"**🎮 Match Schedule:**\n"
                    f"• **Match 1:** Start `{m1_time}` | 📍 Map: `{m1_map}`\n"
                    f"• **Match 2:** Start `{m2_time}` | 📍 Map: `{m2_map}`\n\n"
                    f"⚠️ **RULES & INSTRUCTIONS:**\n"
                    f"1. IDP will be posted here 15 mins before start time.\n"
                    f"2. Join your assigned slot strictly.\n"
                    f"3. 📌 **NO SS = NO POINTS!** Submit screenshot within 30 min of match end."
                ),
                colour=discord.Colour.gold(),
            )
            await ch.send(embed=reminder_embed)

            # 2. Formatted Slot List Embed
            teams_cursor = teams_col().find({
                "guild_id": guild.id, "panel_id": panel_id, "window": window, "group_id": gid
            }).sort("slot_number", 1)
            teams_list = await teams_cursor.to_list(100)
            team_by_slot = {t.get("slot_number"): t.get("team_name") for t in teams_list}

            slot_lines = []
            for slot_no in range(1, cap + 1):
                if slot_no <= res_count and slot_no not in team_by_slot:
                    slot_lines.append(f"`Slot {slot_no:02d}:` 🛡️ **RESERVED**")
                elif slot_no in team_by_slot:
                    slot_lines.append(f"`Slot {slot_no:02d}:` ⚔️ **{team_by_slot[slot_no]}**")
                else:
                    slot_lines.append(f"`Slot {slot_no:02d}:` _OPEN_")

            slot_embed = discord.Embed(
                title=f"📋 OFFICIAL SLOT LIST — {panel_id.upper()} ({gid})",
                description="\n".join(slot_lines),
                colour=discord.Colour.green(),
                timestamp=datetime.now(timezone.utc),
            )
            slot_embed.set_footer(text=f"Total Slots: {cap} • Group {gid}")
            await ch.send(embed=slot_embed)

    async def execute_panel_reset(self, guild: discord.Guild, panel_id: str) -> None:
        """Instant panel reset caller."""
        from bot.tasks.midnight_reset import reset_panel_data
        panel = await panels_col().find_one({"guild_id": guild.id, "panel_id": panel_id})
        if panel:
            await reset_panel_data(guild, panel)

    # ── Register Slash Commands ───────────────────────────────────────────

    def _register_commands(self) -> None:
        group = self.panel_group

        # ── /panel create ─────────────────────────────────────────────

        @group.command(
            name="create",
            description="Create a full multi-group scrim panel (#T1-admin, #T1-reg, #T1-winner, lobbies).",
        )
        @app_commands.describe(
            panel_id="Panel identifier (e.g. T1, T2, T3)",
            window="Registration window label (e.g. 8PM, 9PM)",
            groups="Number of groups (1-20, default 1)",
            match_start="Match start time (YYYY-MM-DD HH:MM UTC) — optional",
        )
        @admin_only()
        async def panel_create(
            interaction: discord.Interaction,
            panel_id: str,
            window: str,
            groups: int = 1,
            match_start: Optional[str] = None,
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            guild = interaction.guild
            guild_id = guild.id
            groups = max(1, min(groups, 20))

            match_start_dt: Optional[datetime] = None
            if match_start:
                try:
                    match_start_dt = datetime.strptime(match_start, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
                except ValueError:
                    return await interaction.followup.send("❌ Invalid date format. Use `YYYY-MM-DD HH:MM`.", ephemeral=True)

            upper = panel_id.upper()

            # Pre-generate schedules
            schedules = []
            for i in range(1, groups + 1):
                schedules.append({
                    "group_id": f"G{i:02d}",
                    "m1_time": "12:00 PM",
                    "m2_time": "12:45 PM",
                    "m1_map": "Erangel",
                    "m2_map": "Miramar",
                    "capacity": DEFAULT_MAX_SLOTS,
                    "reserved_slots": 1,
                    "status": "open",
                })

            # ── 1. Create Category ────────────────────────────────────
            category = await ensure_category(guild, upper, panel_id, guild_id)

            # ── 2. Tag-Access & IDP Roles ─────────────────────────────
            tag_role = await ensure_role(guild, f"{upper}-Tag-Access", panel_id, guild_id)
            lobby_roles = {}
            for i in range(1, groups + 1):
                gid = f"G{i:02d}"
                r = await ensure_role(guild, f"{upper}-{gid}", panel_id, guild_id)
                lobby_roles[gid] = r.id

            # ── 3. Core Channels ──────────────────────────────────────
            tag_overwrites = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False, view_channel=True),
                tag_role: discord.PermissionOverwrite(send_messages=True, view_channel=True),
            }

            admin_role = discord.utils.find(lambda r: r.permissions.administrator, guild.roles)
            admin_overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
            }
            if admin_role:
                admin_overwrites[admin_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)

            admin_ch = await ensure_text_channel(guild, f"{panel_id}-admin", category, panel_id, guild_id, "admin_channel_id", overwrites=admin_overwrites)
            reg_ch = await ensure_text_channel(guild, f"{panel_id}-reg-{window}", category, panel_id, guild_id, "reg_channel_id")
            tag_ch = await ensure_text_channel(guild, f"{panel_id}-tag", category, panel_id, guild_id, "tag_channel_id", overwrites=tag_overwrites)
            conf_ch = await ensure_text_channel(guild, f"{panel_id}-conf", category, panel_id, guild_id, "conf_channel_id")
            slotmng_ch = await ensure_text_channel(guild, f"{panel_id}-slotmng", category, panel_id, guild_id, "slotmng_channel_id")
            winner_ch = await ensure_text_channel(guild, f"{panel_id}-winner", category, panel_id, guild_id, "winner_channel_id")

            # ── 4. Lobby Channels (#T1-group-1...) ────────────────────
            lobby_channels = {}
            for i in range(1, groups + 1):
                gid = f"G{i:02d}"
                role_id = lobby_roles[gid]
                idp_role = guild.get_role(role_id)
                lobby_overwrites = {
                    guild.default_role: discord.PermissionOverwrite(view_channel=False),
                }
                if idp_role:
                    # Visible to IDP role, but send_messages=False until match end
                    lobby_overwrites[idp_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)

                l_ch = await ensure_text_channel(guild, f"{panel_id}-group-{i}", category, panel_id, guild_id, None, overwrites=lobby_overwrites)
                lobby_channels[gid] = l_ch.id

            # ── 5. Upsert Panel Document ──────────────────────────────
            panel_doc = {
                "guild_id": guild_id,
                "panel_id": panel_id,
                "name": f"{upper} Scrims",
                "window": window,
                "group_count": groups,
                "schedules": schedules,
                "default_reserved_slots": 1,
                "allow_multi_group_registration": False,
                "channel_ids": {
                    "category_id": category.id,
                    "reg_channel_id": reg_ch.id,
                    "tag_channel_id": tag_ch.id,
                    "conf_channel_id": conf_ch.id,
                    "slotmng_channel_id": slotmng_ch.id,
                    "admin_channel_id": admin_ch.id,
                    "winner_channel_id": winner_ch.id,
                    "lobby_channels": lobby_channels,
                    "lobby_roles": lobby_roles,
                },
                "role_id": tag_role.id,
                "match_start_time": match_start_dt,
                "match_duration_minutes": 30,
                "cancel_lock_minutes": DEFAULT_CANCEL_LOCK_MINUTES,
                "claim_timeout_minutes": DEFAULT_CLAIM_TIMEOUT_MINUTES,
                "max_slots": DEFAULT_MAX_SLOTS,
                "screenshot_window_minutes": DEFAULT_SCREENSHOT_WINDOW_MINUTES,
                "ss_window_status": "closed",
                "midnight_reset": {
                    "enabled": True, "reset_time": "00:00", "timezone": "Asia/Kolkata",
                    "clear_messages": True, "clear_teams": True, "clear_roles": True, "clear_points": False,
                },
                "points_table": PointsTable().model_dump(),
                "status": "pending",
            }
            await panels_col().update_one(
                {"guild_id": guild_id, "panel_id": panel_id},
                {"$set": panel_doc},
                upsert=True,
            )

            # ── 6. Post Admin Control Panel Embed in #T1-admin ────────
            acp_embed = await self.render_admin_panel_embed(guild_id, panel_id)
            acp_view = AdminControlPanelView(self.bot, panel_id)
            await admin_ch.send(embed=acp_embed, view=acp_view)

            # ── 7. Setup Slot Management ──────────────────────────────
            await self.provision_registration_portal(guild, panel_id)

            await interaction.followup.send(
                f"✅ **{upper} Scrim Panel Provisioned Successfully!**\n"
                f"• Category: {category.name}\n"
                f"• Admin Panel: {admin_ch.mention} (Private)\n"
                f"• Registration: {reg_ch.mention}\n"
                f"• Groups Created: `{groups}` lobbies ({', '.join(f'<#{cid}>' for cid in lobby_channels.values())})\n"
                f"• Winner Announcements: {winner_ch.mention}",
                ephemeral=True,
            )

        # ── /promote_teams (Rate-Limited Winner Promotion) ────────────

        @self.bot.tree.command(
            name="promote_teams",
            description="Award promotion roles to top teams with rate-limit throttling.",
        )
        @app_commands.describe(
            panel_id="Panel ID (e.g. T1)",
            role="Role to award",
            top_count="Number of top teams to promote (e.g. 3)",
        )
        @admin_only()
        async def promote_teams(
            interaction: discord.Interaction,
            panel_id: str,
            role: discord.Role,
            top_count: int = 3,
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            guild_id = interaction.guild_id

            panel = await panels_col().find_one({"guild_id": guild_id, "panel_id": panel_id})
            if not panel:
                return await interaction.followup.send("❌ Panel not found.", ephemeral=True)

            window = panel.get("window", "8PM")

            # Aggregate leaderboards
            pipeline = [
                {"$match": {"guild_id": guild_id, "panel_id": panel_id, "window": window}},
                {"$group": {"_id": "$team_name", "total_points": {"$sum": "$total_points"}}},
                {"$sort": {"total_points": -1}},
                {"$limit": top_count},
            ]
            standings = await points_col().aggregate(pipeline).to_list(top_count)
            if not standings:
                return await interaction.followup.send("❌ No points recorded yet for this panel.", ephemeral=True)

            promoted_players = 0
            promoted_teams_names = []

            for entry in standings:
                t_name = entry["_id"]
                promoted_teams_names.append(t_name)
                team_doc = await teams_col().find_one({
                    "guild_id": guild_id, "panel_id": panel_id, "window": window, "team_name": t_name
                })
                if team_doc:
                    members = team_doc.get("members", [])
                    for mid in members:
                        member = interaction.guild.get_member(mid)
                        if member:
                            try:
                                await member.add_roles(role, reason=f"Promoted from {panel_id} ({t_name})")
                                promoted_players += 1
                                # ⚠️ Rate-Limit Throttling Safeguard: 1s sleep per member
                                await asyncio.sleep(1.0)
                            except Exception as e:
                                log.warning("Could not add role to %s: %s", mid, e)

            # Post announcement to winner channel
            winner_ch_id = panel.get("channel_ids", {}).get("winner_channel_id")
            if winner_ch_id:
                wch = interaction.guild.get_channel(winner_ch_id)
                if wch:
                    win_embed = discord.Embed(
                        title=f"🏆 TIER PROMOTION & WINNERS — {panel_id.upper()}",
                        description=(
                            f"Congratulations to the **Top {len(promoted_teams_names)} Teams** who earned {role.mention}!\n\n"
                            + "\n".join(f"`#{i+1}` **{name}**" for i, name in enumerate(promoted_teams_names))
                        ),
                        colour=discord.Colour.gold(),
                        timestamp=datetime.now(timezone.utc),
                    )
                    await wch.send(embed=win_embed)

            await interaction.followup.send(
                f"✅ **Promotion Completed!**\n"
                f"• Teams: {', '.join(promoted_teams_names)}\n"
                f"• Players Awarded Role: {promoted_players}\n"
                f"• Rate-limiting safely applied (1s delay per role).",
                ephemeral=True,
            )

        # ── /panel reset ──────────────────────────────────────────────

        @group.command(name="reset", description="Manually trigger a full or partial reset of a scrim panel.")
        @app_commands.describe(panel_id="Panel ID (e.g. T1)")
        @admin_only()
        async def panel_manual_reset(interaction: discord.Interaction, panel_id: str) -> None:
            await interaction.response.defer(ephemeral=True)
            await self.execute_panel_reset(interaction.guild, panel_id)
            await interaction.followup.send(f"✅ Panel **{panel_id}** data has been reset.", ephemeral=True)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command("panel", type=discord.AppCommandType.chat_input)
        self.bot.tree.remove_command("promote_teams", type=discord.AppCommandType.chat_input)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PanelCog(bot))
