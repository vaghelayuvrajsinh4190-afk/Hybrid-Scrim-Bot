"""
Idempotent channel, category, and role provisioning.

The three-step lookup guarantees we never create duplicates:
1. Check the stored Discord ID in Mongo — if it resolves, done.
2. If the ID is missing or stale, search the guild by name — if found,
   store the ID and done.
3. If neither, create the resource, store the ID, done.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

from shared.database import panels_col, shared_channels_col

log = logging.getLogger(__name__)


# ── Categories ─────────────────────────────────────────────────────────────

async def ensure_category(
    guild: discord.Guild,
    name: str,
    panel_id: str,
    guild_id: int,
) -> discord.CategoryChannel:
    """Return (or create) the category for a panel and persist its ID."""
    panel = await panels_col().find_one(
        {"guild_id": guild_id, "panel_id": panel_id}
    )
    stored_id = (panel or {}).get("channel_ids", {}).get("category_id")

    # Step 1: try stored ID
    if stored_id:
        cat = guild.get_channel(stored_id)
        if isinstance(cat, discord.CategoryChannel):
            return cat

    # Step 2: search by name
    for cat in guild.categories:
        if cat.name.lower() == name.lower():
            await panels_col().update_one(
                {"guild_id": guild_id, "panel_id": panel_id},
                {"$set": {"channel_ids.category_id": cat.id}},
            )
            return cat

    # Step 3: create
    cat = await guild.create_category(name)
    await panels_col().update_one(
        {"guild_id": guild_id, "panel_id": panel_id},
        {"$set": {"channel_ids.category_id": cat.id}},
    )
    log.info("Created category %s (%s)", cat.name, cat.id)
    return cat


# ── Text Channels ─────────────────────────────────────────────────────────

# ── Text Channels ─────────────────────────────────────────────────────────

async def ensure_text_channel(
    guild: discord.Guild,
    name: str,
    category: discord.CategoryChannel,
    panel_id: str,
    guild_id: int,
    field_key: Optional[str] = None,
    overwrites: Optional[dict] = None,
) -> discord.TextChannel:
    """Return (or create) a text channel under *category* and persist its ID.

    *field_key* is the dot-path suffix inside ``channel_ids``
    (e.g. ``"reg_channel_id"`` or ``"lobby_channels.G01"``).
    """
    panel = await panels_col().find_one(
        {"guild_id": guild_id, "panel_id": panel_id}
    )

    stored_id = None
    if panel and field_key:
        ch_ids = panel.get("channel_ids", {})
        if "." in field_key:
            parent, child = field_key.split(".", 1)
            stored_id = ch_ids.get(parent, {}).get(child)
        else:
            stored_id = ch_ids.get(field_key)

    if stored_id:
        ch = guild.get_channel(stored_id)
        if isinstance(ch, discord.TextChannel):
            return ch

    # Search existing by name under category
    for ch in category.text_channels if category else guild.text_channels:
        if ch.name.lower() == name.lower():
            if field_key:
                await panels_col().update_one(
                    {"guild_id": guild_id, "panel_id": panel_id},
                    {"$set": {f"channel_ids.{field_key}": ch.id}},
                )
            return ch

    # Create channel
    ch = await guild.create_text_channel(
        name, category=category, overwrites=overwrites or {},
    )
    if field_key:
        await panels_col().update_one(
            {"guild_id": guild_id, "panel_id": panel_id},
            {"$set": {f"channel_ids.{field_key}": ch.id}},
        )
    log.info("Created text channel #%s (%s)", ch.name, ch.id)
    return ch


# ── Roles ──────────────────────────────────────────────────────────────────

async def ensure_role(
    guild: discord.Guild,
    name: str,
    panel_id: str,
    guild_id: int,
    colour: discord.Colour | None = None,
    field_key: str = "role_id",
) -> discord.Role:
    """Return (or create) a role for a panel (e.g. tag-access or group IDP role)."""
    panel = await panels_col().find_one(
        {"guild_id": guild_id, "panel_id": panel_id}
    )

    stored_id = None
    if panel:
        if "." in field_key:
            parent, child = field_key.split(".", 1)
            stored_id = panel.get("channel_ids", {}).get(parent, {}).get(child)
        elif field_key == "role_id":
            stored_id = panel.get("role_id")
        else:
            stored_id = panel.get("channel_ids", {}).get(field_key)

    if stored_id:
        role = guild.get_role(stored_id)
        if role is not None:
            return role

    for role in guild.roles:
        if role.name.lower() == name.lower():
            if field_key == "role_id":
                await panels_col().update_one(
                    {"guild_id": guild_id, "panel_id": panel_id},
                    {"$set": {"role_id": role.id}},
                )
            elif field_key:
                await panels_col().update_one(
                    {"guild_id": guild_id, "panel_id": panel_id},
                    {"$set": {f"channel_ids.{field_key}": role.id}},
                )
            return role

    role = await guild.create_role(
        name=name,
        colour=colour or discord.Colour.blue(),
        mentionable=False,
    )
    if field_key == "role_id":
        await panels_col().update_one(
            {"guild_id": guild_id, "panel_id": panel_id},
            {"$set": {"role_id": role.id}},
        )
    elif field_key:
        await panels_col().update_one(
            {"guild_id": guild_id, "panel_id": panel_id},
            {"$set": {f"channel_ids.{field_key}": role.id}},
        )
    log.info("Created role @%s (%s)", role.name, role.id)
    return role


# ── Shared (non-panel) channels ───────────────────────────────────────────

async def ensure_shared_channel(
    guild: discord.Guild,
    name: str,
    guild_id: int,
    field_key: str,
    category: Optional[discord.CategoryChannel] = None,
) -> discord.TextChannel:
    """Return (or create) a guild-level shared channel and persist its ID."""
    doc = await shared_channels_col().find_one({"guild_id": guild_id})
    stored_id = (doc or {}).get(field_key)

    if stored_id:
        ch = guild.get_channel(stored_id)
        if isinstance(ch, discord.TextChannel):
            return ch

    for ch in guild.text_channels:
        if ch.name.lower() == name.lower():
            await shared_channels_col().update_one(
                {"guild_id": guild_id},
                {"$set": {field_key: ch.id}},
                upsert=True,
            )
            return ch

    ch = await guild.create_text_channel(name, category=category)
    await shared_channels_col().update_one(
        {"guild_id": guild_id},
        {"$set": {field_key: ch.id}},
        upsert=True,
    )
    log.info("Created shared channel #%s (%s)", ch.name, ch.id)
    return ch
