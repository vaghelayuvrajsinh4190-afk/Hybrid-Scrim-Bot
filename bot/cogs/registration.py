"""
Registration cog — claim → tag → confirm flow.

Handles:
  - Tag submission (message listener in tag channels)
  - Multi-group routing (G01, G02… prefixed slot numbers)
  - Duplicate-player check (Edge Case 2)
  - Registration confirmation with segmented progress bar
  - Progress bar image generation offloaded to worker thread
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands

from shared.database import panels_col, registrations_col, teams_col
from bot.utils.checks import is_banned
from bot.utils.progress_bar import generate_segmented_bar

log = logging.getLogger(__name__)

# Regex to extract user IDs from mentions
MENTION_RE = re.compile(r"<@!?(\d+)>")


class RegistrationCog(commands.Cog, name="Registration"):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Listen for tag submissions in tag channels."""
        if message.author.bot:
            return
        if message.guild is None:
            return

        guild_id = message.guild.id

        # Is this message in a tag channel?
        panel = await panels_col().find_one({
            "guild_id": guild_id,
            "channel_ids.tag_channel_id": message.channel.id,
        })
        if panel is None:
            return

        panel_id = panel["panel_id"]
        window = panel["window"]
        user_id = message.author.id

        # Must have an active pending claim (includes group_id if multi-group)
        reg = await registrations_col().find_one({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
            "claimer_discord_id": user_id,
            "status": "pending",
        })
        if reg is None:
            await message.reply(
                "❌ You don't have an active slot claim. "
                "Click **Claim Slot** first.",
                delete_after=10,
            )
            return

        # ── Parse tag submission ──────────────────────────────────────
        content = message.content.strip()
        mentions = MENTION_RE.findall(content)
        mentioned_ids = [int(m) for m in mentions]

        # Remove mentions from content to extract team name
        team_name = MENTION_RE.sub("", content).strip()
        # Also remove extra whitespace
        team_name = " ".join(team_name.split())

        if not team_name:
            await message.reply(
                "❌ Please include a **team name** along with your mentions.\n"
                "Format: `TeamName @player1 @player2 @player3 @player4`",
                delete_after=15,
            )
            return

        if len(mentioned_ids) != 4:
            await message.reply(
                f"❌ You need exactly **4 player mentions**, "
                f"got {len(mentioned_ids)}.\n"
                "Format: `TeamName @player1 @player2 @player3 @player4`",
                delete_after=15,
            )
            return

        # Ensure no duplicate mentions
        if len(set(mentioned_ids)) != 4:
            await message.reply(
                "❌ All 4 mentioned players must be **different**.",
                delete_after=10,
            )
            return

        # ── Validate: duplicate team name ─────────────────────────────
        existing_team = await teams_col().find_one({
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
            "team_name": {"$regex": f"^{re.escape(team_name)}$", "$options": "i"},
        })
        if existing_team:
            await message.reply(
                f"❌ Team name **{team_name}** is already taken in this window.",
                delete_after=10,
            )
            return

        # ── Validate: bans ────────────────────────────────────────────
        for mid in mentioned_ids:
            if await is_banned(mid, guild_id):
                await message.reply(
                    f"❌ <@{mid}> is **banned** from scrims.",
                    delete_after=10,
                )
                return

        # ── Edge Case 2: Duplicate player across teams ────────────────
        group_id = reg.get("group_id", "G01")

        # Scope duplicate check to same group
        for mid in mentioned_ids:
            dup_query = {
                "guild_id": guild_id,
                "panel_id": panel_id,
                "window": window,
                "members": mid,
            }
            # If multi-group, duplicate within same group is blocked;
            # cross-group duplicates are allowed only if panel allows it
            if not panel.get("allow_multi_group_registration", False):
                pass  # global scope — dup_query already covers all groups
            else:
                dup_query["group_id"] = group_id  # scope to same group

            dup_team = await teams_col().find_one(dup_query)
            if dup_team:
                await message.reply(
                    f"❌ <@{mid}> is already registered on team "
                    f"**{dup_team['team_name']}** in this window.",
                    delete_after=15,
                )
                return

        # ── All checks passed — register team ─────────────────────────
        # Assign next available slot number within this group
        max_slot_doc = await teams_col().find_one(
            {
                "guild_id": guild_id,
                "panel_id": panel_id,
                "window": window,
                "group_id": group_id,
            },
            sort=[("slot_number", -1)],
        )
        next_num = (max_slot_doc["slot_number"] + 1) if max_slot_doc and max_slot_doc.get("slot_number") else 1
        slot_label = f"{group_id}-{next_num:02d}"  # e.g. G01-03

        team_doc = {
            "team_name": team_name,
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
            "group_id": group_id,
            "owner_discord_id": user_id,
            "members": mentioned_ids,
            "slot_number": next_num,
            "slot_label": slot_label,
            "registered_at": datetime.now(timezone.utc),
            "confirmed": True,
        }
        await teams_col().insert_one(team_doc)

        # Update registration status atomically
        await registrations_col().update_one(
            {"_id": reg["_id"]},
            {"$set": {
                "status": "completed",
                "team_name": team_name,
                "slot_label": slot_label,
            }},
        )

        # Post confirmation to conf channel
        conf_ch_id = panel.get("channel_ids", {}).get("conf_channel_id")
        log.info("Conf channel lookup: conf_ch_id=%s", conf_ch_id)
        if conf_ch_id:
            conf_ch = message.guild.get_channel(conf_ch_id)
            log.info("Conf channel object: %s", conf_ch)
            if conf_ch:
                members_str = " ".join(f"<@{mid}>" for mid in mentioned_ids)
                conf_embed = discord.Embed(
                    title="✅ Team Registered",
                    colour=discord.Colour.green(),
                    timestamp=datetime.now(timezone.utc),
                )
                conf_embed.add_field(name="Team", value=team_name, inline=True)
                conf_embed.add_field(name="Slot", value=slot_label, inline=True)
                conf_embed.add_field(name="Group", value=group_id, inline=True)
                conf_embed.add_field(name="Panel", value=panel_id, inline=True)
                conf_embed.add_field(name="Window", value=window, inline=True)
                conf_embed.add_field(
                    name="Members", value=members_str, inline=False,
                )
                conf_embed.set_footer(text=f"Registered by {message.author}")

                # Include admin action buttons
                from bot.views.persistent import AdminActionsView
                await conf_ch.send(embed=conf_embed, view=AdminActionsView())
                log.info("Sent conf embed to #%s", conf_ch.name)
            else:
                log.warning("Conf channel ID %s not found in guild cache", conf_ch_id)
        else:
            log.warning("No conf_channel_id in panel %s channel_ids: %s", panel_id, panel.get("channel_ids", {}))

        # Also notify in the group-specific lobby channel
        lobby_map = panel.get("channel_ids", {}).get("lobby_channels", {})
        log.info("Lobby map for panel %s: %s | Looking up group_id=%s", panel_id, lobby_map, group_id)
        lobby_ch_id = lobby_map.get(group_id)
        if lobby_ch_id:
            lobby_ch = message.guild.get_channel(lobby_ch_id)
            if lobby_ch:
                lobby_embed = discord.Embed(
                    title=f"📥 {team_name} — Slot {slot_label}",
                    description=" ".join(f"<@{mid}>" for mid in mentioned_ids),
                    colour=discord.Colour.blurple(),
                )
                await lobby_ch.send(embed=lobby_embed)
                log.info("Sent lobby embed to #%s", lobby_ch.name)
            else:
                log.warning("Lobby channel ID %s not found in guild cache", lobby_ch_id)
        else:
            log.warning("No lobby channel found for group_id=%s in lobby_map=%s", group_id, lobby_map)

        # Revoke tag-access role
        role_id = panel.get("role_id")
        if role_id:
            role = message.guild.get_role(role_id)
            if role:
                try:
                    await message.author.remove_roles(
                        role, reason="Tag submitted",
                    )
                except discord.Forbidden:
                    pass

        # Reply in tag channel
        group_lobby_mention = f" | Lobby: <#{lobby_ch_id}>" if lobby_ch_id else ""
        await message.reply(
            f"✅ Team **{team_name}** registered in slot **{slot_label}**! "
            f"Check {f'<#{conf_ch_id}>' if conf_ch_id else 'the confirmation channel'} "
            f"for details.{group_lobby_mention}",
        )

        # Refresh slot board
        cog = self.bot.get_cog("SlotBoard")
        if cog and hasattr(cog, "refresh_board"):
            await cog.refresh_board(guild_id, panel_id)

        # Refresh registration embed slot count + progress bar
        await self._update_reg_embed(message.guild, panel, group_id)

    async def _update_reg_embed(
        self, guild: discord.Guild, panel: dict, group_id: str | None = None,
    ) -> None:
        """Update the registration embed with current slot count + refreshed progress bar."""
        reg_ch_id = panel.get("channel_ids", {}).get("reg_channel_id")
        msg_id = panel.get("reg_message_id")
        if not reg_ch_id or not msg_id:
            return

        ch = guild.get_channel(reg_ch_id)
        if not ch:
            return

        try:
            msg = await ch.fetch_message(msg_id)
        except discord.NotFound:
            return

        # Per-group or global capacity count
        count_query = {
            "guild_id": panel["guild_id"],
            "panel_id": panel["panel_id"],
            "window": panel["window"],
            "status": {"$in": ["pending", "completed"]},
        }
        filled = await registrations_col().count_documents(count_query)
        max_slots = panel.get("max_slots", 20)

        # Build per-group breakdown for embed description & fields
        group_count = panel.get("group_count", 1)
        schedules = panel.get("schedules", [])
        group_lines = []
        new_fields = []
        for i in range(1, group_count + 1):
            gid = f"G{i:02d}"
            gfilled = await registrations_col().count_documents({
                **count_query, "group_id": gid,
            })
            grp_sched = next((s for s in schedules if s.get("group_id") == gid), {})
            gcap = grp_sched.get("capacity", max_slots)
            bar_str = make_circle_bar(gfilled, gcap)
            group_lines.append(f"`{gid}`: {bar_str} ({gfilled}/{gcap})")

            m1 = grp_sched.get("m1_time", "12:00 PM")
            m2 = grp_sched.get("m2_time", "12:45 PM")
            map1 = grp_sched.get("m1_map", "Erangel")
            map2 = grp_sched.get("m2_map", "Miramar")
            new_fields.append({
                "name": f"🎮 Lobby {gid} ({gfilled}/{gcap} Slots)",
                "value": f"`{bar_str}` ({gfilled}/{gcap})\n• Match 1: `{m1}` ({map1})\n• Match 2: `{m2}` ({map2})",
                "inline": False,
            })

        groups_summary = "\n".join(group_lines) if group_lines else ""

        if msg.embeds:
            embed = msg.embeds[0]
            embed.clear_fields()
            for f in new_fields:
                embed.add_field(name=f["name"], value=f["value"], inline=f["inline"])

            embed.description = (
                f"**Window:** {panel['window']}\n"
                f"**Total Slots:** {filled} / {max_slots}\n\n"
            )
            if groups_summary:
                embed.description += f"**Live Groups:**\n{groups_summary}\n\n"
            embed.description += "Click the button below to claim a slot.\n"
            await msg.edit(embed=embed, attachments=[])


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RegistrationCog(bot))
