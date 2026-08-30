"""
Screenshot submission and review — Lobby submission, Private Thread Approval View, and !approve / !reject.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from shared.config import VERIFICATION_TTL_DAYS
from shared.database import panels_col, verifications_col
from bot.views.persistent import ScreenshotApprovalView

log = logging.getLogger(__name__)


class Screenshots(commands.Cog):
    """Handles screenshot submission in lobby channels and admin review."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.ctx_menu = app_commands.ContextMenu(
            name="Review Screenshot",
            callback=self.review_screenshot_ctx,
        )
        self.bot.tree.add_command(self.ctx_menu)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.ctx_menu.name, type=self.ctx_menu.type)

    async def review_screenshot_ctx(
        self, interaction: discord.Interaction, message: discord.Message
    ) -> None:
        """Context menu: Admin right clicks screenshot message to review ephemerally."""
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ Admin only.", ephemeral=True)

        doc = await verifications_col().find_one({
            "guild_id": interaction.guild_id,
            "submitted_by": message.author.id,
            "status": "pending",
        })

        if not doc:
            return await interaction.response.send_message(
                "❌ No pending verification found for this submission.", ephemeral=True
            )

        embed = discord.Embed(
            title=f"📸 Admin Review — {doc.get('team_name')}",
            description=f"Submitted by <@{doc['submitted_by']}>\nGroup: {doc.get('group_id', 'G01')}",
            colour=discord.Colour.blue(),
        )
        urls = doc.get("screenshot_urls", [])
        if urls:
            embed.set_image(url=urls[0])

        view = ScreenshotApprovalView(verification_id=str(doc["_id"]), public_message_id=message.id)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None:
            return

        guild_id = message.guild.id

        # Find panel matching this lobby channel or conf channel
        panel = await panels_col().find_one({
            "guild_id": guild_id,
            "$or": [
                {"channel_ids.conf_channel_id": message.channel.id},
                {f"channel_ids.lobby_channels.{k}": message.channel.id for k in [f"G{i:02d}" for i in range(1, 21)]},
            ]
        })

        if panel is None:
            # Check by iterating lobby_channels map if dynamic
            panel = await panels_col().find_one({
                "guild_id": guild_id,
            })
            if panel:
                lobby_map = panel.get("channel_ids", {}).get("lobby_channels", {})
                if message.channel.id not in lobby_map.values() and message.channel.id != panel.get("channel_ids", {}).get("conf_channel_id"):
                    return
            else:
                return

        # Determine which group this channel is
        group_id = "G01"
        lobby_map = panel.get("channel_ids", {}).get("lobby_channels", {})
        for gid, ch_id in lobby_map.items():
            if ch_id == message.channel.id:
                group_id = gid
                break

        # Only process messages with image attachments
        images = [
            a for a in message.attachments
            if a.content_type and a.content_type.startswith("image/")
        ]
        if len(images) < 1:
            return

        # Check screenshot window status
        ss_status = panel.get("ss_window_status", "closed")
        if ss_status != "open":
            await message.reply(
                "⏱️ Screenshot submissions are currently **closed** for this match.\n"
                "Screenshots are only accepted during the 30-minute window after match end.",
                delete_after=15,
            )
            return

        # Look for a team name in the message content
        team_name = message.content.strip()
        if not team_name:
            await message.reply(
                "📸 Please include your **Team Name** in your message with the screenshots.\n"
                "Example: `TeamAlpha` (with images attached)",
                delete_after=15,
            )
            return

        # Create verification document
        screenshot_urls = [a.url for a in images]
        now = datetime.now(timezone.utc)

        doc = {
            "team_name": team_name,
            "guild_id": guild_id,
            "panel_id": panel["panel_id"],
            "window": panel["window"],
            "group_id": group_id,
            "screenshot_urls": screenshot_urls,
            "submitted_by": message.author.id,
            "submitted_at": now,
            "status": "pending",
            "reviewed_by": None,
            "reviewed_at": None,
            "expires_at": None,
        }
        res = await verifications_col().insert_one(doc)
        verification_id = str(res.inserted_id)

        # 1. Public confirmation in lobby channel (players see this — no buttons)
        lobby_embed = discord.Embed(
            title=f"📸 Screenshot Submitted — {team_name}",
            description=(
                f"**Team:** {team_name} ({group_id})\n"
                f"**Submitted by:** {message.author.mention}\n"
                f"**Attachments:** {len(images)} image(s)\n\n"
                "⏳ **Status:** Under Admin Review"
            ),
            colour=discord.Colour.orange(),
            timestamp=now,
        )
        if images:
            lobby_embed.set_thumbnail(url=images[0].url)
        public_msg = await message.reply(embed=lobby_embed)

        # 2. Create Discord Private Thread inside #T1-group-X (ONLY admins can see)
        try:
            safe_team = "".join(c for c in team_name if c.isalnum() or c in ("-", "_")).lower()[:20]
            thread = await message.channel.create_thread(
                name=f"🔒-review-{safe_team}",
                type=discord.ChannelType.private_thread,
                auto_archive_duration=60,
                reason=f"Admin screenshot review for {team_name}",
            )

            admin_embed = discord.Embed(
                title=f"📸 Admin Review — {team_name} ({group_id})",
                description=(
                    f"**Submitted by:** {message.author.mention} (`{message.author.id}`)\n"
                    f"**Channel:** {message.channel.mention}\n"
                    f"**Images Attached:** {len(images)}\n"
                    f"**Public Message:** [Jump to Message]({public_msg.jump_url})"
                ),
                colour=discord.Colour.blue(),
                timestamp=now,
            )
            admin_embed.set_image(url=images[0].url)
            admin_embed.set_footer(text=f"💡 Or type: !approve {team_name}  |  !reject {team_name} [reason]")

            view = ScreenshotApprovalView(verification_id=verification_id, public_message_id=public_msg.id)
            await thread.send(embed=admin_embed, view=view)
        except discord.Forbidden:
            log.warning("Bot lacks permission to create private threads in %s", message.channel.name)
        except Exception as e:
            log.error("Failed to create admin review thread: %s", e)

    # ── !approve <team> ────────────────────────────────────────────────

    @commands.command(name="approve")
    @commands.has_permissions(administrator=True)
    async def approve_cmd(
        self, ctx: commands.Context, *, team_name: str
    ) -> None:
        guild_id = ctx.guild.id
        now = datetime.now(timezone.utc)
        expires = now + timedelta(days=VERIFICATION_TTL_DAYS)

        result = await verifications_col().update_one(
            {
                "guild_id": guild_id,
                "team_name": {"$regex": f"^{team_name}$", "$options": "i"},
                "status": "pending",
            },
            {
                "$set": {
                    "status": "approved",
                    "reviewed_by": ctx.author.id,
                    "reviewed_at": now,
                    "expires_at": expires,
                },
            },
        )

        if result.modified_count == 0:
            await ctx.reply(f"❌ No pending verification found for **{team_name}**.")
            return

        await ctx.reply(f"✅ Screenshots for **{team_name}** approved!")

    # ── !reject <team> [reason] ────────────────────────────────────────

    @commands.command(name="reject")
    @commands.has_permissions(administrator=True)
    async def reject_cmd(
        self, ctx: commands.Context, *, args: str
    ) -> None:
        parts = args.split(maxsplit=1)
        team_name = parts[0]
        reason = parts[1] if len(parts) > 1 else "No reason provided"

        guild_id = ctx.guild.id
        now = datetime.now(timezone.utc)

        result = await verifications_col().update_one(
            {
                "guild_id": guild_id,
                "team_name": {"$regex": f"^{team_name}$", "$options": "i"},
                "status": "pending",
            },
            {
                "$set": {
                    "status": "rejected",
                    "reviewed_by": ctx.author.id,
                    "reviewed_at": now,
                    "rejection_reason": reason,
                },
            },
        )

        if result.modified_count == 0:
            await ctx.reply(f"❌ No pending verification found for **{team_name}**.")
            return

        await ctx.reply(
            f"❌ Screenshots for **{team_name}** rejected.\n**Reason:** {reason}"
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Screenshots(bot))
