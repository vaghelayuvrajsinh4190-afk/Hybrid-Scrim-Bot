"""
All Modal classes used by the bot.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import discord
from discord import ui

from shared.database import panels_col, players_col

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

