"""
APScheduler engine for automated registration open / close.

The scheduler runs in UTC.  Jobs are added with ``'date'`` triggers
and stable IDs so they can be replaced or recovered after a restart.
"""

from __future__ import annotations

import logging

import discord
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from pytz import utc

from shared.database import panels_col

scrim_scheduler = AsyncIOScheduler(timezone=utc)
logger = logging.getLogger("scrim_scheduler")


async def trigger_open(
    bot: discord.Client,
    channel_id: int,
    panel_id: str,
    guild_id: int,
) -> None:
    """Fired when the scheduled open time is reached."""
    try:
        channel = await bot.fetch_channel(channel_id)
        await channel.set_permissions(
            channel.guild.default_role, send_messages=True,
        )

        embed = discord.Embed(
            title="🟢 Registrations are OPEN!",
            description="Tag your 4 players below to secure your slot.",
            color=discord.Color.green(),
        )
        await channel.send(embed=embed)

        await panels_col().update_one(
            {"guild_id": guild_id, "panel_id": panel_id},
            {"$set": {"status": "open"}},
        )
        logger.info(
            "Successfully opened panel %s in channel %s", panel_id, channel_id,
        )
    except discord.Forbidden:
        logger.error("Missing permissions to open channel %s", channel_id)
    except Exception as e:
        logger.error("Failed to open panel %s: %s", panel_id, e)


async def trigger_close(
    bot: discord.Client,
    channel_id: int,
    panel_id: str,
    guild_id: int,
) -> None:
    """Fired when the scheduled close time is reached."""
    try:
        channel = await bot.fetch_channel(channel_id)
        await channel.set_permissions(
            channel.guild.default_role, send_messages=False,
        )

        embed = discord.Embed(
            title="🔴 Registrations are CLOSED.",
            description="No more tags will be accepted.",
            color=discord.Color.red(),
        )
        await channel.send(embed=embed)

        await panels_col().update_one(
            {"guild_id": guild_id, "panel_id": panel_id},
            {"$set": {"status": "closed"}},
        )
        logger.info(
            "Successfully closed panel %s in channel %s", panel_id, channel_id,
        )
    except discord.Forbidden:
        logger.error("Missing permissions to close channel %s", channel_id)
    except Exception as e:
        logger.error("Failed to close panel %s: %s", panel_id, e)
