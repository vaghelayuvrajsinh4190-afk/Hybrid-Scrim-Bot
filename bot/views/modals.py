"""
All Modal classes used by the bot.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone

import discord
from discord import ui

from shared.database import panels_col, players_col
from bot.utils.channel_ops import ensure_category, ensure_role, ensure_text_channel

log = logging.getLogger(__name__)


# ── Link ID Modal ──────────────────────────────────────────────────────────

class LinkIDModal(ui.Modal, title="Link Your BGMI ID"):
    bgmi_id = ui.TextInput(
        label="BGMI Character ID (10 digits)",
        placeholder="e.g. 5123456789",
        min_length=10,
        max_length=10,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        raw = self.bgmi_id.value.strip()
        if not raw.isdigit() or len(raw) != 10:
            await interaction.response.send_message(
                "❌ Invalid ID — must be exactly 10 digits.", ephemeral=True,
            )
            return

        now = datetime.now(timezone.utc)
        result = await players_col().find_one({
            "discord_id": interaction.user.id,
            "guild_id": interaction.guild_id,
        })
        if result:
            await players_col().update_one(
                {"_id": result["_id"]},
                {"$set": {"bgmi_id": raw, "updated_at": now}},
            )
            await interaction.response.send_message(
                f"✅ Your BGMI ID has been **updated** to `{raw}`.",
                ephemeral=True,
            )
        else:
            await players_col().insert_one({
                "discord_id": interaction.user.id,
                "guild_id": interaction.guild_id,
                "bgmi_id": raw,
                "linked_at": now,
                "updated_at": now,
            })
            await interaction.response.send_message(
                f"✅ BGMI ID `{raw}` linked successfully!", ephemeral=True,
            )


# ── Panel Settings Modal ──────────────────────────────────────────────────

class PanelSettingsModal(ui.Modal, title="Panel Settings"):
    def __init__(self, panel_id: str, current: dict) -> None:
        super().__init__()
        self._panel_id = panel_id
        self._guild_id = current.get("guild_id", 0)

        mst = current.get("match_start_time")
        mst_str = mst.strftime("%Y-%m-%d %H:%M") if mst else ""

        self.match_start = ui.TextInput(
            label="Match Start (YYYY-MM-DD HH:MM UTC)",
            default=mst_str,
            required=False,
            max_length=20,
        )
        self.cancel_lock = ui.TextInput(
            label="Cancel Lock (minutes before match)",
            default=str(current.get("cancel_lock_minutes", 60)),
            required=False,
            max_length=5,
        )
        self.claim_timeout = ui.TextInput(
            label="Claim Timeout (minutes)",
            default=str(current.get("claim_timeout_minutes", 5)),
            required=False,
            max_length=5,
        )
        self.max_slots = ui.TextInput(
            label="Max Slots",
            default=str(current.get("max_slots", 20)),
            required=False,
            max_length=5,
        )
        self.points_json = ui.TextInput(
            label="Points Table (JSON)",
            style=discord.TextStyle.paragraph,
            default=json.dumps(current.get("points_table", {}), indent=2),
            required=False,
            max_length=1000,
        )

        self.add_item(self.match_start)
        self.add_item(self.cancel_lock)
        self.add_item(self.claim_timeout)
        self.add_item(self.max_slots)
        self.add_item(self.points_json)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        updates: dict = {}
        errors: list[str] = []

        # Match start
        raw_ms = self.match_start.value.strip()
        if raw_ms:
            try:
                dt = datetime.strptime(raw_ms, "%Y-%m-%d %H:%M").replace(
                    tzinfo=timezone.utc,
                )
                updates["match_start_time"] = dt
            except ValueError:
                errors.append("Invalid date format for match start.")

        # Cancel lock
        raw_cl = self.cancel_lock.value.strip()
        if raw_cl:
            if raw_cl.isdigit():
                updates["cancel_lock_minutes"] = int(raw_cl)
            else:
                errors.append("Cancel lock must be a number.")

        # Claim timeout
        raw_ct = self.claim_timeout.value.strip()
        if raw_ct:
            if raw_ct.isdigit():
                updates["claim_timeout_minutes"] = int(raw_ct)
            else:
                errors.append("Claim timeout must be a number.")

        # Max slots
        raw_ms2 = self.max_slots.value.strip()
        if raw_ms2:
            if raw_ms2.isdigit():
                updates["max_slots"] = int(raw_ms2)
            else:
                errors.append("Max slots must be a number.")

        # Points table
        raw_pt = self.points_json.value.strip()
        if raw_pt:
            try:
                pt = json.loads(raw_pt)
                updates["points_table"] = pt
            except json.JSONDecodeError:
                errors.append("Points table is not valid JSON.")

        if errors:
            await interaction.response.send_message(
                "⚠️ Errors:\n" + "\n".join(f"• {e}" for e in errors),
                ephemeral=True,
            )
            return

        if updates:
            await panels_col().update_one(
                {"guild_id": self._guild_id, "panel_id": self._panel_id},
                {"$set": updates},
            )

        await interaction.response.send_message(
            f"✅ Panel **{self._panel_id}** settings updated.", ephemeral=True,
        )


# ── Panel Rename Modal ────────────────────────────────────────────────────

class PanelRenameModal(ui.Modal, title="Rename Panel Channels"):
    def __init__(self, panel_id: str, current_names: dict) -> None:
        super().__init__()
        self._panel_id = panel_id

        self.reg_name = ui.TextInput(
            label="Registration Channel Name",
            default=current_names.get("reg", ""),
            required=False,
            max_length=100,
        )
        self.tag_name = ui.TextInput(
            label="Tag Channel Name",
            default=current_names.get("tag", ""),
            required=False,
            max_length=100,
        )
        self.conf_name = ui.TextInput(
            label="Confirmation Channel Name",
            default=current_names.get("conf", ""),
            required=False,
            max_length=100,
        )
        self.slotmng_name = ui.TextInput(
            label="Slot Management Channel Name",
            default=current_names.get("slotmng", ""),
            required=False,
            max_length=100,
        )

        self.add_item(self.reg_name)
        self.add_item(self.tag_name)
        self.add_item(self.conf_name)
        self.add_item(self.slotmng_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        # Actual rename + cooldown enforcement is handled by the cog
        # that opened this modal — it reads .rename_values after submit.
        self.rename_values = {
            "reg": self.reg_name.value.strip(),
            "tag": self.tag_name.value.strip(),
            "conf": self.conf_name.value.strip(),
            "slotmng": self.slotmng_name.value.strip(),
        }
        await interaction.response.defer(ephemeral=True)


# ── Schedule Registration Modal ───────────────────────────────────────────

class ScheduleModal(ui.Modal, title="Schedule Lobby Registration"):
    """Admin modal for setting registration open/close times (IST input)."""

    open_time = ui.TextInput(
        label="Open Time (YYYY-MM-DD HH:MM) IST",
        placeholder="e.g., 2026-08-29 14:00",
        required=True,
    )
    close_time = ui.TextInput(
        label="Close Time (YYYY-MM-DD HH:MM) IST",
        placeholder="e.g., 2026-08-29 16:00",
        required=True,
    )

    def __init__(
        self,
        bot: discord.Client,
        panel_id: str,
        channel_id: int,
        guild_id: int,
    ) -> None:
        super().__init__()
        self.bot = bot
        self.panel_id = panel_id
        self.channel_id = channel_id
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from zoneinfo import ZoneInfo
        from bot.tasks.scheduler import scrim_scheduler, trigger_open, trigger_close

        try:
            naive_open = datetime.strptime(self.open_time.value.strip(), "%Y-%m-%d %H:%M")
            naive_close = datetime.strptime(self.close_time.value.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            await interaction.response.send_message(
                "❌ Invalid date format. Please use `YYYY-MM-DD HH:MM`.",
                ephemeral=True,
            )
            return

        ist_zone = ZoneInfo("Asia/Kolkata")
        utc_zone = ZoneInfo("UTC")

        utc_open = naive_open.replace(tzinfo=ist_zone).astimezone(utc_zone)
        utc_close = naive_close.replace(tzinfo=ist_zone).astimezone(utc_zone)

        now_utc = datetime.now(timezone.utc)
        if utc_open <= now_utc:
            await interaction.response.send_message(
                "❌ Open time must be in the future.", ephemeral=True,
            )
            return
        if utc_close <= utc_open:
            await interaction.response.send_message(
                "❌ Close time must be after open time.", ephemeral=True,
            )
            return

        # Persist to DB
        await panels_col().update_one(
            {"guild_id": self.guild_id, "panel_id": self.panel_id},
            {"$set": {
                "schedule_open": utc_open,
                "schedule_close": utc_close,
                "status": "scheduled",
            }},
        )

        # Schedule APScheduler jobs
        scrim_scheduler.add_job(
            trigger_open, "date", run_date=utc_open,
            args=[self.bot, self.channel_id, self.panel_id, self.guild_id],
            id=f"open_{self.panel_id}", replace_existing=True,
        )
        scrim_scheduler.add_job(
            trigger_close, "date", run_date=utc_close,
            args=[self.bot, self.channel_id, self.panel_id, self.guild_id],
            id=f"close_{self.panel_id}", replace_existing=True,
        )

        unix_open = int(utc_open.timestamp())
        unix_close = int(utc_close.timestamp())

        await interaction.response.send_message(
            f"✅ **Lobby Scheduled Successfully!**\n"
            f"**Opens:** <t:{unix_open}:F> (<t:{unix_open}:R>)\n"
            f"**Closes:** <t:{unix_close}:F> (<t:{unix_close}:R>)",
            ephemeral=True,
        )


# ── Screenshot Rejection Reason Modal ──────────────────────────────────────

class RejectReasonModal(ui.Modal, title="Reject Screenshot"):
    reason = ui.TextInput(
        label="Rejection Reason",
        style=discord.TextStyle.paragraph,
        placeholder="e.g. Incomplete scoreboard / missing team name / invalid format",
        required=True,
        max_length=500,
    )

    def __init__(self, verification_id: str, public_message_id: int | None = None) -> None:
        super().__init__()
        self._verification_id = verification_id
        self._public_message_id = public_message_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from bson import ObjectId
        from shared.database import verifications_col

        now = datetime.now(timezone.utc)
        try:
            query = {"_id": ObjectId(self._verification_id), "status": "pending"}
        except Exception:
            query = {"_id": self._verification_id, "status": "pending"}

        result = await verifications_col().update_one(
            query,
            {"$set": {
                "status": "rejected",
                "reviewed_by": interaction.user.id,
                "reviewed_at": now,
                "rejection_reason": self.reason.value.strip(),
            }},
        )

        if result.modified_count == 0:
            await interaction.response.send_message(
                "⚠️ Screenshot was already reviewed or not found.", ephemeral=True,
            )
            return

        # Disable buttons on thread message if possible
        if interaction.message:
            try:
                for item in interaction.message.components:
                    for child in getattr(item, "children", []):
                        child.disabled = True
                await interaction.message.edit(
                    content=f"❌ **Rejected by {interaction.user.mention}**\n**Reason:** {self.reason.value.strip()}"
                )
            except Exception:
                pass

        await interaction.response.send_message(
            f"❌ Screenshot marked **Rejected**.\n**Reason:** {self.reason.value.strip()}",
            ephemeral=True,
        )


# ── Groups Modal ───────────────────────────────────────────────────────────

class GroupsModal(ui.Modal, title="Configure Panel Groups"):
    group_count = ui.TextInput(
        label="Number of Groups (1-20)",
        placeholder="e.g. 2",
        required=True,
        max_length=2,
    )

    def __init__(self, panel_id: str, guild_id: int, current_count: int = 1) -> None:
        super().__init__()
        self.panel_id = panel_id
        self.guild_id = guild_id
        self.group_count.default = str(current_count)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        val = self.group_count.value.strip()
        if not val.isdigit() or not (1 <= int(val) <= 20):
            await interaction.followup.send(
                "❌ Group count must be a number between 1 and 20.", ephemeral=True,
            )
            return

        count = int(val)
        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("❌ Guild not found.", ephemeral=True)

        panel = await panels_col().find_one({"guild_id": self.guild_id, "panel_id": self.panel_id})
        if not panel:
            return await interaction.followup.send("❌ Panel not found.", ephemeral=True)

        upper = self.panel_id.upper()
        schedules = panel.get("schedules", [])
        ch_ids = panel.get("channel_ids", {})
        cat_id = ch_ids.get("category_id")
        category = guild.get_channel(cat_id) if cat_id else None
        if not category:
            category = await ensure_category(guild, upper, self.panel_id, self.guild_id)

        lobby_channels = ch_ids.get("lobby_channels", {})
        lobby_roles = ch_ids.get("lobby_roles", {})

        # Adjust schedules array & dynamically create missing roles and channels
        new_schedules = []
        for i in range(1, count + 1):
            gid = f"G{i:02d}"
            existing_match = next((s for s in schedules if s.get("group_id") == gid), None)
            if existing_match:
                new_schedules.append(existing_match)
            else:
                new_schedules.append({
                    "group_id": gid,
                    "m1_time": "12:00 PM",
                    "m2_time": "12:45 PM",
                    "m1_map": "Erangel",
                    "m2_map": "Miramar",
                    "capacity": panel.get("max_slots", 20),
                    "reserved_slots": panel.get("default_reserved_slots", 1),
                    "status": "open",
                })

            # Ensure group IDP role
            r = await ensure_role(guild, f"{upper}-{gid}", self.panel_id, self.guild_id, field_key=f"lobby_roles.{gid}")
            lobby_roles[gid] = r.id

            # Ensure lobby channel with overwrites
            lobby_overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
            }
            if r:
                lobby_overwrites[r] = discord.PermissionOverwrite(view_channel=True, send_messages=False)

            l_ch = await ensure_text_channel(
                guild, f"{self.panel_id}-group-{i}", category, self.panel_id, self.guild_id,
                field_key=f"lobby_channels.{gid}", overwrites=lobby_overwrites,
            )
            lobby_channels[gid] = l_ch.id
            await asyncio.sleep(0.3)

        await panels_col().update_one(
            {"guild_id": self.guild_id, "panel_id": self.panel_id},
            {"$set": {
                "group_count": count,
                "schedules": new_schedules,
                "channel_ids.lobby_channels": lobby_channels,
                "channel_ids.lobby_roles": lobby_roles,
            }},
        )

        channels_text = ", ".join(f"<#{cid}>" for cid in lobby_channels.values())
        await interaction.followup.send(
            f"✅ **Panel {upper} updated to {count} Groups!**\n"
            f"• Lobbies active: {channels_text}\n"
            f"• Click **🚀 Post to Reg Portal** in this channel to update the registration buttons for all {count} groups.",
            ephemeral=True,
        )


# ── Group Schedule Modal (replaces Bulk Schedule) ─────────────────────────

class GroupScheduleModal(ui.Modal, title="Schedule Match Times (Per Group)"):
    """Group-specific scheduling: IDP times, start times, and maps."""

    group_id = ui.TextInput(
        label="Select Group (e.g. G01, G02)",
        placeholder="G01",
        required=True,
        max_length=3,
    )
    m1_times = ui.TextInput(
        label="Match 1 — IDP Time | Start Time",
        placeholder="07:30 PM | 08:00 PM",
        required=True,
        max_length=30,
    )
    m2_times = ui.TextInput(
        label="Match 2 — IDP Time | Start Time",
        placeholder="08:30 PM | 09:00 PM",
        required=True,
        max_length=30,
    )
    m1_map = ui.TextInput(label="Match 1 Map", default="Erangel", required=False, max_length=30)
    m2_map = ui.TextInput(label="Match 2 Map", default="Miramar", required=False, max_length=30)

    def __init__(self, panel_id: str, guild_id: int) -> None:
        super().__init__()
        self.panel_id = panel_id
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        panel = await panels_col().find_one({"guild_id": self.guild_id, "panel_id": self.panel_id})
        if not panel:
            return await interaction.response.send_message("❌ Panel not found.", ephemeral=True)

        gid = self.group_id.value.strip().upper()
        if not gid.startswith("G") or not gid[1:].isdigit():
            return await interaction.response.send_message(
                "❌ Invalid group ID. Use format: `G01`, `G02`, etc.", ephemeral=True,
            )

        # Parse pipe-separated IDP|Start times
        def _parse_times(raw: str) -> tuple[str, str]:
            parts = [p.strip() for p in raw.split("|")]
            if len(parts) == 2:
                return parts[0], parts[1]
            # Fallback: treat entire value as start time, IDP blank
            return "", parts[0]

        m1_idp, m1_start = _parse_times(self.m1_times.value)
        m2_idp, m2_start = _parse_times(self.m2_times.value)
        map1 = self.m1_map.value.strip() or "Erangel"
        map2 = self.m2_map.value.strip() or "Miramar"

        schedules = panel.get("schedules", [])
        found = False
        for s in schedules:
            if s.get("group_id") == gid:
                s["m1_idp_time"] = m1_idp
                s["m2_idp_time"] = m2_idp
                s["m1_time"] = m1_start
                s["m2_time"] = m2_start
                s["m1_map"] = map1
                s["m2_map"] = map2
                found = True
                break

        if not found:
            return await interaction.response.send_message(
                f"❌ Group **{gid}** does not exist in this panel. "
                f"Available groups: {', '.join(s.get('group_id', '?') for s in schedules)}",
                ephemeral=True,
            )

        await panels_col().update_one(
            {"guild_id": self.guild_id, "panel_id": self.panel_id},
            {"$set": {"schedules": schedules}},
        )

        await interaction.response.send_message(
            f"✅ **{gid} Schedule Updated!**\n"
            f"• Match 1: IDP `{m1_idp or 'N/A'}` → Start `{m1_start}` ({map1})\n"
            f"• Match 2: IDP `{m2_idp or 'N/A'}` → Start `{m2_start}` ({map2})",
            ephemeral=True,
        )


# ── Slots Config Modal ────────────────────────────────────────────────────

class SlotsConfigModal(ui.Modal, title="Slots & Capacity Configuration"):
    capacity = ui.TextInput(label="Capacity Per Lobby", default="20", required=True, max_length=3)
    default_reserved = ui.TextInput(label="Default Reserved Slots Count", default="1", required=True, max_length=2)
    multi_lobby = ui.TextInput(label="Multi-Lobby Registration? (yes/no)", default="no", required=True, max_length=3)

    def __init__(self, panel_id: str, guild_id: int) -> None:
        super().__init__()
        self.panel_id = panel_id
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not self.capacity.value.strip().isdigit() or not self.default_reserved.value.strip().isdigit():
            return await interaction.response.send_message("❌ Capacity and Reserved must be numbers.", ephemeral=True)

        cap = int(self.capacity.value.strip())
        res = int(self.default_reserved.value.strip())
        allow_multi = self.multi_lobby.value.strip().lower() in ["yes", "y", "true", "1"]

        panel = await panels_col().find_one({"guild_id": self.guild_id, "panel_id": self.panel_id})
        schedules = panel.get("schedules", []) if panel else []
        for s in schedules:
            s["capacity"] = cap
            s["reserved_slots"] = res

        await panels_col().update_one(
            {"guild_id": self.guild_id, "panel_id": self.panel_id},
            {"$set": {
                "max_slots": cap,
                "default_reserved_slots": res,
                "allow_multi_group_registration": allow_multi,
                "schedules": schedules,
            }},
        )

        await interaction.response.send_message(
            f"✅ Slot settings saved:\n"
            f"• Capacity: {cap} slots\n"
            f"• Reserved: {res} slots\n"
            f"• Multi-Lobby Registration: {'Allowed' if allow_multi else 'Disabled (1 Lobby Only)'}",
            ephemeral=True,
        )


# ── Midnight Reset Modal ──────────────────────────────────────────────────

class MidnightResetModal(ui.Modal, title="Midnight Scrims Reset Settings"):
    enabled = ui.TextInput(label="Auto Midnight Reset (yes/no)", default="yes", max_length=3)
    clear_msg = ui.TextInput(label="Purge Chat in Tag/Lobbies? (yes/no)", default="yes", max_length=3)
    clear_tm = ui.TextInput(label="Reset Teams & Registrations? (yes/no)", default="yes", max_length=3)
    clear_rl = ui.TextInput(label="Revoke IDP & Tag Roles? (yes/no)", default="yes", max_length=3)

    def __init__(self, panel_id: str, guild_id: int, current: dict | None = None) -> None:
        super().__init__()
        self.panel_id = panel_id
        self.guild_id = guild_id
        if current:
            self.enabled.default = "yes" if current.get("enabled", True) else "no"
            self.clear_msg.default = "yes" if current.get("clear_messages", True) else "no"
            self.clear_tm.default = "yes" if current.get("clear_teams", True) else "no"
            self.clear_rl.default = "yes" if current.get("clear_roles", True) else "no"

    async def on_submit(self, interaction: discord.Interaction) -> None:
        en = self.enabled.value.strip().lower() in ["yes", "y", "true", "1"]
        c_msg = self.clear_msg.value.strip().lower() in ["yes", "y", "true", "1"]
        c_tm = self.clear_tm.value.strip().lower() in ["yes", "y", "true", "1"]
        c_rl = self.clear_rl.value.strip().lower() in ["yes", "y", "true", "1"]

        cfg = {
            "enabled": en,
            "reset_time": "00:00",
            "timezone": "Asia/Kolkata",
            "clear_messages": c_msg,
            "clear_teams": c_tm,
            "clear_roles": c_rl,
            "clear_points": False,
        }

        await panels_col().update_one(
            {"guild_id": self.guild_id, "panel_id": self.panel_id},
            {"$set": {"midnight_reset": cfg}},
        )

        await interaction.response.send_message(
            f"✅ **Midnight Reset Settings Saved for {self.panel_id}:**\n"
            f"• Enabled: {'🟢 Yes' if en else '🔴 No'}\n"
            f"• Purge Chat: {'Yes' if c_msg else 'No'}\n"
            f"• Clear Teams: {'Yes' if c_tm else 'No'}\n"
            f"• Revoke Roles: {'Yes' if c_rl else 'No'}",
            ephemeral=True,
        )


# ── Transfer Slot Modal (Admin) ───────────────────────────────────────────

class TransferSlotModal(ui.Modal, title="Transfer Slot (Admin)"):
    """Admin modal: select source team and destination slot to re-assign."""

    source_team = ui.TextInput(
        label="Source Team Name (exact)",
        placeholder="e.g. Team Alpha",
        required=True,
        max_length=100,
    )
    dest_group = ui.TextInput(
        label="Destination Group",
        placeholder="e.g. G01",
        required=True,
        max_length=3,
    )
    dest_slot = ui.TextInput(
        label="Destination Slot Number",
        placeholder="e.g. 5",
        required=True,
        max_length=3,
    )

    def __init__(self, panel_id: str, guild_id: int) -> None:
        super().__init__()
        self.panel_id = panel_id
        self.guild_id = guild_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        from shared.database import registrations_col, teams_col

        team_name = self.source_team.value.strip()
        dest_gid = self.dest_group.value.strip().upper()
        raw_slot = self.dest_slot.value.strip()

        if not raw_slot.isdigit():
            return await interaction.response.send_message(
                "❌ Slot number must be a number.", ephemeral=True,
            )
        dest_slot_num = int(raw_slot)

        # Validate group exists
        panel = await panels_col().find_one(
            {"guild_id": self.guild_id, "panel_id": self.panel_id}
        )
        if not panel:
            return await interaction.response.send_message("❌ Panel not found.", ephemeral=True)

        valid_groups = [s.get("group_id") for s in panel.get("schedules", [])]
        if dest_gid not in valid_groups:
            return await interaction.response.send_message(
                f"❌ Group **{dest_gid}** not found. Available: {', '.join(valid_groups)}",
                ephemeral=True,
            )

        window = panel.get("window", "8PM")

        # Find source team
        team = await teams_col().find_one({
            "guild_id": self.guild_id,
            "panel_id": self.panel_id,
            "window": window,
            "team_name": {"$regex": f"^{team_name}$", "$options": "i"},
        })
        if not team:
            return await interaction.response.send_message(
                f"❌ Team **{team_name}** not found in this panel/window.",
                ephemeral=True,
            )

        # Check if destination slot is occupied
        occupant = await teams_col().find_one({
            "guild_id": self.guild_id,
            "panel_id": self.panel_id,
            "window": window,
            "group_id": dest_gid,
            "slot_number": dest_slot_num,
        })
        if occupant and occupant.get("team_name", "").lower() != team_name.lower():
            return await interaction.response.send_message(
                f"❌ Slot {dest_slot_num} in **{dest_gid}** is occupied by "
                f"**{occupant['team_name']}**. Free it first.",
                ephemeral=True,
            )

        old_group = team.get("group_id", "G01")
        new_label = f"{dest_gid}-{dest_slot_num:02d}"

        # Atomically update team
        await teams_col().update_one(
            {"_id": team["_id"]},
            {"$set": {
                "group_id": dest_gid,
                "slot_number": dest_slot_num,
                "slot_label": new_label,
            }},
        )

        # Update registration
        await registrations_col().update_one(
            {
                "guild_id": self.guild_id,
                "panel_id": self.panel_id,
                "window": window,
                "claimer_discord_id": team["owner_discord_id"],
                "group_id": old_group,
            },
            {"$set": {
                "group_id": dest_gid,
                "slot_label": new_label,
            }},
        )

        # Swap IDP roles on the owner
        guild = interaction.guild
        ch_ids = panel.get("channel_ids", {})
        role_map = ch_ids.get("lobby_roles", {})
        owner = guild.get_member(team["owner_discord_id"])
        if owner:
            # Remove old group role
            old_role_id = role_map.get(old_group)
            if old_role_id:
                old_role = guild.get_role(old_role_id)
                if old_role:
                    try:
                        await owner.remove_roles(old_role, reason="Slot transferred")
                    except discord.Forbidden:
                        pass
            # Add new group role
            new_role_id = role_map.get(dest_gid)
            if new_role_id:
                new_role = guild.get_role(new_role_id)
                if new_role:
                    try:
                        await owner.add_roles(new_role, reason="Slot transferred")
                    except discord.Forbidden:
                        pass

        await interaction.response.send_message(
            f"✅ **Slot Transferred!**\n"
            f"• Team: **{team['team_name']}**\n"
            f"• From: `{old_group}` → To: `{dest_gid}` Slot `{dest_slot_num}`\n"
            f"• New Label: `{new_label}`",
            ephemeral=True,
        )


# ── Role Transfer Modal ───────────────────────────────────────────────────

class RoleTransferModal(ui.Modal, title="Role Transfer to Teammate"):
    """Player modal: transfer slot ownership to a registered teammate."""

    target_user = ui.TextInput(
        label="Target User ID or @mention",
        placeholder="e.g. 123456789012345678 or @username",
        required=True,
        max_length=30,
    )

    def __init__(self, panel_id: str, guild_id: int, user_id: int) -> None:
        super().__init__()
        self.panel_id = panel_id
        self.guild_id = guild_id
        self.user_id = user_id

    async def on_submit(self, interaction: discord.Interaction) -> None:
        import re
        from shared.database import registrations_col, teams_col

        # Parse target user ID from mention or raw ID
        raw = self.target_user.value.strip()
        match = re.search(r"(\d{15,20})", raw)
        if not match:
            return await interaction.response.send_message(
                "❌ Could not parse a user ID. Provide a numeric Discord ID or @mention.",
                ephemeral=True,
            )
        target_id = int(match.group(1))

        if target_id == self.user_id:
            return await interaction.response.send_message(
                "❌ You cannot transfer to yourself.", ephemeral=True,
            )

        panel = await panels_col().find_one(
            {"guild_id": self.guild_id, "panel_id": self.panel_id}
        )
        if not panel:
            return await interaction.response.send_message("❌ Panel not found.", ephemeral=True)

        window = panel.get("window", "8PM")

        # Find sender's team
        team = await teams_col().find_one({
            "guild_id": self.guild_id,
            "panel_id": self.panel_id,
            "window": window,
            "owner_discord_id": self.user_id,
        })
        if not team:
            return await interaction.response.send_message(
                "❌ You don't have an active team registration to transfer.",
                ephemeral=True,
            )

        # Verify target is on the SAME team
        if target_id not in team.get("members", []):
            return await interaction.response.send_message(
                f"❌ <@{target_id}> is **not a member** of your team "
                f"**{team['team_name']}**. Role transfer is only allowed "
                f"within the same team.",
                ephemeral=True,
            )

        guild = interaction.guild
        target_member = guild.get_member(target_id)
        if not target_member:
            return await interaction.response.send_message(
                f"❌ <@{target_id}> is not in this server.", ephemeral=True,
            )

        group_id = team.get("group_id", "G01")
        ch_ids = panel.get("channel_ids", {})
        role_map = ch_ids.get("lobby_roles", {})

        # Strip role from sender, assign to target
        role_id = role_map.get(group_id)
        sender_member = guild.get_member(self.user_id)
        if role_id:
            role = guild.get_role(role_id)
            if role:
                if sender_member:
                    try:
                        await sender_member.remove_roles(role, reason="Role transfer — sender")
                    except discord.Forbidden:
                        pass
                try:
                    await target_member.add_roles(role, reason="Role transfer — receiver")
                except discord.Forbidden:
                    pass

        # Update ownership in DB
        await teams_col().update_one(
            {"_id": team["_id"]},
            {"$set": {"owner_discord_id": target_id}},
        )
        await registrations_col().update_one(
            {
                "guild_id": self.guild_id,
                "panel_id": self.panel_id,
                "window": window,
                "claimer_discord_id": self.user_id,
            },
            {"$set": {"claimer_discord_id": target_id}},
        )

        await interaction.response.send_message(
            f"✅ **Role Transferred!**\n"
            f"• Team: **{team['team_name']}** ({group_id})\n"
            f"• From: <@{self.user_id}> → To: <@{target_id}>\n"
            f"• IDP/Group role reassigned.",
            ephemeral=True,
        )
