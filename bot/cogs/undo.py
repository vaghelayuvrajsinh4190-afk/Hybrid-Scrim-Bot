"""
Undo system — soft-delete panel messages with a 5-minute restore window.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from shared.config import RECENTLY_DELETED_TTL_SECONDS
from shared.database import recently_deleted_col
from bot.utils.checks import admin_only

log = logging.getLogger(__name__)


class Undo(commands.Cog):
    """Soft-delete + undo for panel messages."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="delete_message",
        description="Soft-delete a message by ID (restorable for 5 minutes).",
    )
    @app_commands.describe(
        message_id="The message ID to soft-delete",
        channel="Channel the message is in (defaults to current)",
    )
    @admin_only()
    async def delete_message_cmd(
        self,
        interaction: discord.Interaction,
        message_id: str,
        channel: discord.TextChannel | None = None,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        target_channel = channel or interaction.channel
        try:
            msg = await target_channel.fetch_message(int(message_id))
        except (discord.NotFound, ValueError):
            await interaction.followup.send("❌ Message not found.")
            return

        # Snapshot
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=RECENTLY_DELETED_TTL_SECONDS)
        snapshot = {
            "content": msg.content,
            "embeds": [e.to_dict() for e in msg.embeds],
            "channel_id": target_channel.id,
            "message_id": msg.id,
            "author_id": msg.author.id,
        }

        result = await recently_deleted_col().insert_one({
            "original_collection": "messages",
            "original_id": str(msg.id),
            "snapshot": snapshot,
            "deleted_by": interaction.user.id,
            "deleted_at": now,
            "expires_at": expires,
        })

        try:
            await msg.delete()
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Missing permissions to delete that message."
            )
            return

        await interaction.followup.send(
            f"🗑️ Message deleted. Use `/undo {result.inserted_id}` "
            f"to restore within {RECENTLY_DELETED_TTL_SECONDS // 60} minutes.",
        )

    @app_commands.command(
        name="undo",
        description="Restore a soft-deleted message.",
    )
    @app_commands.describe(snapshot_id="The snapshot ID from the delete confirmation")
    @admin_only()
    async def undo_cmd(
        self,
        interaction: discord.Interaction,
        snapshot_id: str,
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        from bson import ObjectId

        try:
            oid = ObjectId(snapshot_id)
        except Exception:
            await interaction.followup.send("❌ Invalid snapshot ID.")
            return

        doc = await recently_deleted_col().find_one_and_delete({"_id": oid})
        if doc is None:
            await interaction.followup.send(
                "⏱️ Undo expired — snapshot already cleaned up by TTL."
            )
            return

        snap = doc["snapshot"]
        ch = interaction.guild.get_channel(snap["channel_id"])
        if ch is None:
            await interaction.followup.send(
                "❌ Original channel no longer exists."
            )
            return

        embeds = [discord.Embed.from_dict(e) for e in snap.get("embeds", [])]
        content = snap.get("content", "") or None

        await ch.send(content=content, embeds=embeds)
        await interaction.followup.send("✅ Message restored!")

    @app_commands.command(
        name="recent_deletes",
        description="List messages awaiting undo.",
    )
    @admin_only()
    async def recent_deletes_cmd(
        self, interaction: discord.Interaction
    ) -> None:
        await interaction.response.defer(ephemeral=True)

        cursor = recently_deleted_col().find({
            "original_collection": "messages",
        }).sort("deleted_at", -1).limit(10)

        docs = await cursor.to_list(length=10)
        if not docs:
            await interaction.followup.send("ℹ️ No recent deletes pending.")
            return

        lines = []
        for d in docs:
            snap = d.get("snapshot", {})
            ch_id = snap.get("channel_id", 0)
            deleted_by = d.get("deleted_by", 0)
            expires = d.get("expires_at")
            exp_str = (
                f"<t:{int(expires.timestamp())}:R>" if expires else "unknown"
            )
            lines.append(
                f"**ID:** `{d['_id']}` • <#{ch_id}> • "
                f"by <@{deleted_by}> • expires {exp_str}"
            )

        embed = discord.Embed(
            title="🗑️ Recent Deletes (Restorable)",
            description="\n".join(lines),
            colour=discord.Colour.orange(),
        )
        await interaction.followup.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Undo(bot))
