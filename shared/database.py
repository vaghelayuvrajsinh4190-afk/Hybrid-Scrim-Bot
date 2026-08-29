"""
Database layer — Motor (async MongoDB) client, collection accessors,
and index creation.

Both the bot and dashboard import from here so they share the exact
same connection logic and collection references.
"""

from __future__ import annotations

import logging
from typing import Optional

import pymongo
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from shared.config import DB_NAME, MONGODB_URI

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton client / db references — initialised lazily
# ---------------------------------------------------------------------------
_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


def get_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        if not MONGODB_URI:
            raise RuntimeError("MONGODB_URI is not set")
        _client = AsyncIOMotorClient(MONGODB_URI)
    return _client


def get_db() -> AsyncIOMotorDatabase:
    global _db
    if _db is None:
        _db = get_client()[DB_NAME]
    return _db


# ---------------------------------------------------------------------------
# Collection accessors — thin wrappers so callers never hard-code names
# ---------------------------------------------------------------------------
def panels_col():
    return get_db()["panels"]

def players_col():
    return get_db()["players"]

def teams_col():
    return get_db()["teams"]

def registrations_col():
    return get_db()["registrations"]

def bans_col():
    return get_db()["bans"]

def verifications_col():
    return get_db()["verifications"]

def points_col():
    return get_db()["points"]

def groups_col():
    return get_db()["groups"]

def recently_deleted_col():
    return get_db()["recently_deleted"]

def logs_col():
    return get_db()["logs"]

def shared_channels_col():
    return get_db()["shared_channels"]


# ---------------------------------------------------------------------------
# Index creation — idempotent, safe to call on every startup
# ---------------------------------------------------------------------------
async def ensure_indexes() -> None:
    """Create all required indexes.  Idempotent (no-ops if they exist)."""
    log.info("Ensuring MongoDB indexes …")

    # ── panels ─────────────────────────────────────────────────────────
    await panels_col().create_index(
        [("guild_id", pymongo.ASCENDING), ("panel_id", pymongo.ASCENDING)],
        unique=True,
        name="uq_guild_panel",
    )

    # ── players ────────────────────────────────────────────────────────
    await players_col().create_index(
        [("discord_id", pymongo.ASCENDING), ("guild_id", pymongo.ASCENDING)],
        unique=True,
        name="uq_player_guild",
    )

    # ── teams ──────────────────────────────────────────────────────────
    await teams_col().create_index(
        [
            ("guild_id", pymongo.ASCENDING),
            ("panel_id", pymongo.ASCENDING),
            ("window", pymongo.ASCENDING),
        ],
        name="idx_teams_panel_window",
    )
    # Compound index including members — needed for the duplicate-player
    # check during registration.  MongoDB can use a multikey index on
    # the `members` array field so the $elemMatch / $in query is indexed.
    await teams_col().create_index(
        [
            ("guild_id", pymongo.ASCENDING),
            ("panel_id", pymongo.ASCENDING),
            ("window", pymongo.ASCENDING),
            ("members", pymongo.ASCENDING),
        ],
        name="idx_teams_dup_player_check",
    )

    # ── registrations ──────────────────────────────────────────────────
    await registrations_col().create_index(
        [
            ("guild_id", pymongo.ASCENDING),
            ("panel_id", pymongo.ASCENDING),
            ("window", pymongo.ASCENDING),
        ],
        name="idx_reg_panel_window",
    )
    await registrations_col().create_index(
        [
            ("guild_id", pymongo.ASCENDING),
            ("panel_id", pymongo.ASCENDING),
            ("window", pymongo.ASCENDING),
            ("claimer_discord_id", pymongo.ASCENDING),
        ],
        name="idx_reg_claimer",
    )

    # ── bans — TTL on optional expires_at ──────────────────────────────
    await bans_col().create_index(
        [("discord_id", pymongo.ASCENDING), ("guild_id", pymongo.ASCENDING)],
        name="idx_ban_player_guild",
    )
    await bans_col().create_index(
        "expires_at",
        expireAfterSeconds=0,
        name="ttl_ban_expiry",
        partialFilterExpression={"expires_at": {"$exists": True}},
    )

    # ── verifications — TTL 7 days post-review ─────────────────────────
    await verifications_col().create_index(
        "expires_at",
        expireAfterSeconds=0,
        name="ttl_verification_expiry",
        partialFilterExpression={"expires_at": {"$exists": True}},
    )

    # ── points ─────────────────────────────────────────────────────────
    await points_col().create_index(
        [
            ("guild_id", pymongo.ASCENDING),
            ("panel_id", pymongo.ASCENDING),
            ("window", pymongo.ASCENDING),
        ],
        name="idx_points_panel_window",
    )

    # ── groups ─────────────────────────────────────────────────────────
    await groups_col().create_index(
        [("guild_id", pymongo.ASCENDING), ("group_number", pymongo.ASCENDING)],
        unique=True,
        name="uq_group_number",
    )

    # ── recently_deleted — TTL auto-purge ──────────────────────────────
    await recently_deleted_col().create_index(
        "expires_at",
        expireAfterSeconds=0,
        name="ttl_recently_deleted",
    )

    # ── logs ───────────────────────────────────────────────────────────
    await logs_col().create_index(
        [("guild_id", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)],
        name="idx_logs_guild_time",
    )

    # ── shared_channels ────────────────────────────────────────────────
    await shared_channels_col().create_index(
        "guild_id",
        unique=True,
        name="uq_shared_channels_guild",
    )

    log.info("MongoDB indexes ready.")


async def close() -> None:
    """Gracefully close the Motor client."""
    global _client, _db
    if _client is not None:
        _client.close()
        _client = None
        _db = None
        log.info("MongoDB connection closed.")
