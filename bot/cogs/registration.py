"""
Registration cog — claim → tag → confirm flow.

Handles:
  - Tag submission (message listener in tag channels)
  - Duplicate-player check (Edge Case 2)
  - Registration confirmation
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

import discord
from discord.ext import commands

from shared.database import panels_col, registrations_col, teams_col
from bot.utils.checks import is_banned

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

        # Must have an active pending claim
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
        for mid in mentioned_ids:
            dup_team = await teams_col().find_one({
                "guild_id": guild_id,
                "panel_id": panel_id,
                "window": window,
                "members": mid,
            })
            if dup_team:
                await message.reply(
                    f"❌ <@{mid}> is already registered on team "
                    f"**{dup_team['team_name']}** in this window.",
                    delete_after=15,
                )
                return

        # ── All checks passed — register team ─────────────────────────
        # Assign next available slot number
        max_slot_doc = await teams_col().find_one(
            {"guild_id": guild_id, "panel_id": panel_id, "window": window},
            sort=[("slot_number", -1)],
        )
        next_slot = (max_slot_doc["slot_number"] + 1) if max_slot_doc and max_slot_doc.get("slot_number") else 1

        team_doc = {
            "team_name": team_name,
            "guild_id": guild_id,
            "panel_id": panel_id,
            "window": window,
            "owner_discord_id": user_id,
            "members": mentioned_ids,
            "slot_number": next_slot,
            "registered_at": datetime.now(timezone.utc),
            "confirmed": True,
        }
        await teams_col().insert_one(team_doc)

        # Update registration status
        await registrations_col().update_one(
            {"_id": reg["_id"]},
            {"$set": {
                "status": "completed",
                "team_name": team_name,
            }},
        )

        # Post confirmation to conf channel
        conf_ch_id = panel.get("channel_ids", {}).get("conf_channel_id")
        if conf_ch_id:
            conf_ch = message.guild.get_channel(conf_ch_id)
            if conf_ch:
                members_str = " ".join(f"<@{mid}>" for mid in mentioned_ids)
                conf_embed = discord.Embed(
                    title="✅ Team Registered",
                    colour=discord.Colour.green(),
                    timestamp=datetime.now(timezone.utc),
                )
                conf_embed.add_field(name="Team", value=team_name, inline=True)
                conf_embed.add_field(name="Slot", value=f"#{next_slot}", inline=True)
                conf_embed.add_field(name="Panel", value=panel_id, inline=True)
                conf_embed.add_field(name="Window", value=window, inline=True)
                conf_embed.add_field(
                    name="Members", value=members_str, inline=False,
                )
                conf_embed.set_footer(text=f"Registered by {message.author}")

                # Include admin action buttons
                from bot.views.persistent import AdminActionsView
                await conf_ch.send(embed=conf_embed, view=AdminActionsView())

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
        await message.reply(
            f"✅ Team **{team_name}** registered in slot **#{next_slot}**! "
            f"Check {f'<#{conf_ch_id}>' if conf_ch_id else 'the confirmation channel'} "
            f"for details.",
        )

        # Refresh slot board
        cog = self.bot.get_cog("SlotBoard")
        if cog and hasattr(cog, "refresh_board"):
            await cog.refresh_board(guild_id, panel_id)

        # Refresh registration embed slot count
        await self._update_reg_embed(message.guild, panel)

    async def _update_reg_embed(
        self, guild: discord.Guild, panel: dict,
    ) -> None:
        """Update the registration embed to reflect current slot count."""
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

        filled = await registrations_col().count_documents({
            "guild_id": panel["guild_id"],
            "panel_id": panel["panel_id"],
            "window": panel["window"],
            "status": {"$in": ["pending", "completed"]},
        })
        max_slots = panel.get("max_slots", 20)

        if msg.embeds:
            embed = msg.embeds[0]
            embed.description = (
                f"**Window:** {panel['window']}\n"
                f"**Slots:** {filled} / {max_slots}\n\n"
                "Click the button below to claim a slot.\n"
            )
            await msg.edit(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RegistrationCog(bot))
