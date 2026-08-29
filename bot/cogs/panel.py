"""
Panel management cog.

Commands:
  /panel create <panel_id> <window> [match_start] [cancel_lock_minutes]
  /panel settings <panel_id>
  /panel channels <panel_id>
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from shared.config import (
    DEFAULT_CANCEL_LOCK_MINUTES,
    DEFAULT_CLAIM_TIMEOUT_MINUTES,
    DEFAULT_MAX_SLOTS,
    DEFAULT_SCREENSHOT_WINDOW_MINUTES,
)
from shared.database import panels_col
from shared.models import ChannelIds, PanelConfig, PointsTable

from bot.utils.channel_ops import ensure_category, ensure_role, ensure_text_channel
from bot.utils.checks import admin_only
from bot.utils.cooldown import check_rename_allowed, record_rename
from bot.views.modals import PanelRenameModal, PanelSettingsModal
from bot.views.persistent import ClaimSlotView, PanelControlView

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

    def _register_commands(self) -> None:
        group = self.panel_group

        # ── /panel create ─────────────────────────────────────────────

        @group.command(
            name="create",
            description="Create a new scrim panel with dedicated channels and role.",
        )
        @app_commands.describe(
            panel_id="Panel identifier (e.g. T1, T2, T3)",
            window="Registration window label (e.g. week1, day3)",
            match_start="Match start time (YYYY-MM-DD HH:MM UTC) — optional",
            cancel_lock_minutes="Minutes before match to lock cancels (default 60)",
        )
        @admin_only()
        async def panel_create(
            interaction: discord.Interaction,
            panel_id: str,
            window: str,
            match_start: Optional[str] = None,
            cancel_lock_minutes: Optional[int] = None,
        ) -> None:
            await interaction.response.defer(ephemeral=True)
            guild = interaction.guild
            guild_id = guild.id

            # Parse optional match start time
            match_start_dt: Optional[datetime] = None
            if match_start:
                try:
                    match_start_dt = datetime.strptime(
                        match_start, "%Y-%m-%d %H:%M"
                    ).replace(tzinfo=timezone.utc)
                except ValueError:
                    await interaction.followup.send(
                        "❌ Invalid date format. Use `YYYY-MM-DD HH:MM`.",
                        ephemeral=True,
                    )
                    return

            cancel_lock = cancel_lock_minutes or DEFAULT_CANCEL_LOCK_MINUTES

            # Upsert panel doc first (needed by channel_ops for ID storage)
            existing = await panels_col().find_one({
                "guild_id": guild_id, "panel_id": panel_id,
            })
            if not existing:
                panel_doc = PanelConfig(
                    guild_id=guild_id,
                    panel_id=panel_id,
                    window=window,
                    match_start_time=match_start_dt,
                    cancel_lock_minutes=cancel_lock,
                    claim_timeout_minutes=DEFAULT_CLAIM_TIMEOUT_MINUTES,
                    max_slots=DEFAULT_MAX_SLOTS,
                    screenshot_window_minutes=DEFAULT_SCREENSHOT_WINDOW_MINUTES,
                    points_table=PointsTable(),
                ).model_dump()
                await panels_col().insert_one(panel_doc)
            else:
                # Update window and settings for re-runs
                await panels_col().update_one(
                    {"_id": existing["_id"]},
                    {"$set": {
                        "window": window,
                        "match_start_time": match_start_dt,
                        "cancel_lock_minutes": cancel_lock,
                    }},
                )

            # ── Idempotent provisioning ───────────────────────────────
            upper = panel_id.upper()

            # Category
            category = await ensure_category(
                guild, upper, panel_id, guild_id,
            )

            # Tag-access role
            role = await ensure_role(
                guild, f"{upper}-Tag-Access", panel_id, guild_id,
            )

            # Permission overwrites for tag channel (only role holders can type)
            tag_overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    send_messages=False, view_channel=True,
                ),
                role: discord.PermissionOverwrite(
                    send_messages=True, view_channel=True,
                ),
            }

            # Channels
            reg_ch = await ensure_text_channel(
                guild,
                f"{panel_id}-reg-{window}",
                category,
                panel_id,
                guild_id,
                "reg_channel_id",
            )
            tag_ch = await ensure_text_channel(
                guild,
                f"{panel_id}-tag",
                category,
                panel_id,
                guild_id,
                "tag_channel_id",
                overwrites=tag_overwrites,
            )
            conf_ch = await ensure_text_channel(
                guild,
                f"{panel_id}-conf",
                category,
                panel_id,
                guild_id,
                "conf_channel_id",
            )
            slotmng_ch = await ensure_text_channel(
                guild,
                f"{panel_id}-slotmng",
                category,
                panel_id,
                guild_id,
                "slotmng_channel_id",
            )

            # ── Post registration embed with Claim Slot button ────────
            embed = discord.Embed(
                title=f"🏆 {upper} Scrim Registration",
                description=(
                    f"**Window:** {window}\n"
                    f"**Slots:** 0 / {DEFAULT_MAX_SLOTS}\n\n"
                    "Click the button below to claim a slot.\n"
                    f"After claiming, post your team in {tag_ch.mention} "
                    f"within {DEFAULT_CLAIM_TIMEOUT_MINUTES} minutes."
                ),
                colour=discord.Colour.gold(),
                timestamp=datetime.now(timezone.utc),
            )
            if match_start_dt:
                embed.add_field(
                    name="Match Start",
                    value=discord.utils.format_dt(match_start_dt, "F"),
                )
            embed.set_footer(text=f"Panel {upper}")

            view = ClaimSlotView(panel_id=panel_id)
            msg = await reg_ch.send(embed=embed, view=view)

            # Store the message ID for later updates
            await panels_col().update_one(
                {"guild_id": guild_id, "panel_id": panel_id},
                {"$set": {"reg_message_id": msg.id}},
            )

            # ── Post Panel Control embed to admin panel channel ───────
            control_embed = discord.Embed(
                title=f"🛠️ {upper} — Panel Control",
                description=(
                    "Use the button below to schedule when registration\n"
                    "automatically opens and closes for this panel."
                ),
                colour=discord.Colour.blurple(),
                timestamp=datetime.now(timezone.utc),
            )
            control_embed.set_footer(text=f"Panel {upper}")

            control_view = PanelControlView(self.bot, panel_id)
            control_msg = await slotmng_ch.send(
                embed=control_embed, view=control_view,
            )
            await panels_col().update_one(
                {"guild_id": guild_id, "panel_id": panel_id},
                {"$set": {"control_message_id": control_msg.id}},
            )

            await interaction.followup.send(
                f"✅ Panel **{upper}** provisioned!\n"
                f"• Category: {category.mention}\n"
                f"• Registration: {reg_ch.mention}\n"
                f"• Tag: {tag_ch.mention}\n"
                f"• Confirmation: {conf_ch.mention}\n"
                f"• Slot Mgmt: {slotmng_ch.mention}\n"
                f"• Role: @{role.name}",
                ephemeral=True,
            )

        # ── /panel settings ───────────────────────────────────────────

        @group.command(
            name="settings",
            description="Edit panel settings (match start, timeouts, points table).",
        )
        @app_commands.describe(panel_id="Panel identifier (e.g. T1)")
        @admin_only()
        async def panel_settings(
            interaction: discord.Interaction,
            panel_id: str,
        ) -> None:
            panel = await panels_col().find_one({
                "guild_id": interaction.guild_id,
                "panel_id": panel_id,
            })
            if panel is None:
                await interaction.response.send_message(
                    f"❌ Panel **{panel_id}** not found.", ephemeral=True,
                )
                return

            modal = PanelSettingsModal(panel_id, panel)
            await interaction.response.send_modal(modal)

        # ── /panel channels ───────────────────────────────────────────

        @group.command(
            name="channels",
            description="Rename panel channels (respects rate-limit cooldown).",
        )
        @app_commands.describe(panel_id="Panel identifier (e.g. T1)")
        @admin_only()
        async def panel_channels(
            interaction: discord.Interaction,
            panel_id: str,
        ) -> None:
            guild = interaction.guild
            guild_id = guild.id

            panel = await panels_col().find_one({
                "guild_id": guild_id, "panel_id": panel_id,
            })
            if panel is None:
                await interaction.response.send_message(
                    f"❌ Panel **{panel_id}** not found.", ephemeral=True,
                )
                return

            ch_ids = panel.get("channel_ids", {})
            current_names = {}
            for key, field in [
                ("reg", "reg_channel_id"),
                ("tag", "tag_channel_id"),
                ("conf", "conf_channel_id"),
                ("slotmng", "slotmng_channel_id"),
            ]:
                cid = ch_ids.get(field)
                if cid:
                    ch = guild.get_channel(cid)
                    current_names[key] = ch.name if ch else ""
                else:
                    current_names[key] = ""

            modal = PanelRenameModal(panel_id, current_names)
            await interaction.response.send_modal(modal)

            # Wait for modal submission
            if await modal.wait():
                return  # timed out

            rename_vals = getattr(modal, "rename_values", {})
            results: list[str] = []
            field_map = {
                "reg": "reg_channel_id",
                "tag": "tag_channel_id",
                "conf": "conf_channel_id",
                "slotmng": "slotmng_channel_id",
            }

            for key, new_name in rename_vals.items():
                if not new_name:
                    continue
                field = field_map[key]
                cid = ch_ids.get(field)
                if not cid:
                    continue
                ch = guild.get_channel(cid)
                if not ch:
                    continue
                if ch.name == new_name:
                    continue

                # Cooldown check
                allowed, remaining = await check_rename_allowed(
                    guild_id, panel_id, cid,
                )
                if not allowed:
                    mins = int(remaining // 60) + 1
                    results.append(
                        f"⏳ #{ch.name}: rename blocked — "
                        f"try again in ~{mins} min",
                    )
                    continue

                try:
                    await ch.edit(name=new_name)
                    await record_rename(guild_id, panel_id, cid)
                    results.append(f"✅ #{new_name}")
                except discord.HTTPException as exc:
                    results.append(f"❌ #{ch.name}: {exc}")

            summary = "\n".join(results) if results else "No changes."
            await interaction.followup.send(summary, ephemeral=True)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command("panel", type=discord.AppCommandType.chat_input)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PanelCog(bot))
