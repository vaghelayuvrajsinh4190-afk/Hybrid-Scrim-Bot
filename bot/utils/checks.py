"""
Permission and ban checks used across cogs.
"""

from __future__ import annotations

from datetime import datetime, timezone

import discord

from shared.database import bans_col


async def is_banned(discord_id: int, guild_id: int) -> bool:
    """Return True if the user has an active (non-expired) ban."""
    doc = await bans_col().find_one({
        "discord_id": discord_id,
        "guild_id": guild_id,
    })
    if doc is None:
        return False
    # If expires_at exists and is in the past, Mongo TTL may not have
    # cleaned it yet — treat as not banned.
    exp = doc.get("expires_at")
    if exp is not None and exp <= datetime.now(timezone.utc):
        return False
    return True


def is_admin(interaction: discord.Interaction) -> bool:
    """Check whether the invoking member has Administrator permission."""
    if interaction.guild is None:
        return False
    perms = interaction.user.guild_permissions  # type: ignore[union-attr]
    return perms.administrator


def admin_only():
    """discord.py app_commands check decorator for admin-only commands."""
    from discord import app_commands

    async def predicate(interaction: discord.Interaction) -> bool:
        if not is_admin(interaction):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission to use this command.",
                ephemeral=True,
            )
            return False
        return True

    return app_commands.check(predicate)
