"""
Pydantic v2 models — shared between the Discord bot and FastAPI dashboard.

Every document that touches MongoDB is defined here so both processes
serialise / deserialise identically.  Field names match the Mongo document
keys exactly (no aliases needed).
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── helpers ────────────────────────────────────────────────────────────────
def _utcnow() -> datetime:
    """Timezone-aware UTC timestamp factory for default values."""
    return datetime.now(timezone.utc)


# ── enums ──────────────────────────────────────────────────────────────────
class RegistrationStatus(str, enum.Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    EXPIRED = "expired"


class VerificationStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


# ── sub-models ─────────────────────────────────────────────────────────────
class PointsTable(BaseModel):
    """Per-panel configurable scoring formula."""
    kill_weight: float = 1.0
    placement_weights: Dict[int, float] = Field(
        default_factory=lambda: {
            1: 15, 2: 12, 3: 10, 4: 8, 5: 6,
            6: 4, 7: 2, 8: 1, 9: 0, 10: 0,
            11: 0, 12: 0, 13: 0, 14: 0, 15: 0, 16: 0,
        },
        description="Mapping of placement rank → bonus points.",
    )


class ChannelIds(BaseModel):
    """Discord channel IDs provisioned for a single panel."""
    category_id: Optional[int] = None
    reg_channel_id: Optional[int] = None
    tag_channel_id: Optional[int] = None
    conf_channel_id: Optional[int] = None
    slotmng_channel_id: Optional[int] = None


# ── top-level documents ───────────────────────────────────────────────────
class PanelConfig(BaseModel):
    """One scrim panel (T1, T2, …).  Stored in ``panels`` collection."""
    guild_id: int
    panel_id: str
    window: str
    channel_ids: ChannelIds = Field(default_factory=ChannelIds)
    role_id: Optional[int] = None
    match_start_time: Optional[datetime] = None
    cancel_lock_minutes: int = 60
    claim_timeout_minutes: int = 5
    max_slots: int = 20
    screenshot_window_minutes: int = 30
    points_table: PointsTable = Field(default_factory=PointsTable)
    rename_history: Dict[str, List[float]] = Field(
        default_factory=dict,
        description="channel/role id (str) → list of rename epoch timestamps",
    )
    reg_message_id: Optional[int] = None
    slotboard_message_id: Optional[int] = None
    control_message_id: Optional[int] = None
    status: str = Field(default="pending")  # pending | scheduled | open | closed
    schedule_open: Optional[datetime] = None
    schedule_close: Optional[datetime] = None
    created_at: datetime = Field(default_factory=_utcnow)


class Player(BaseModel):
    """A linked BGMI player.  Permanent collection."""
    discord_id: int
    guild_id: int
    bgmi_id: Optional[str] = None
    linked_at: Optional[datetime] = None
    updated_at: datetime = Field(default_factory=_utcnow)


class Team(BaseModel):
    """A registered team for a specific panel + window.  Permanent."""
    team_name: str
    guild_id: int
    panel_id: str
    window: str
    owner_discord_id: int
    members: List[int] = Field(
        default_factory=list,
        description="List of Discord user IDs (exactly 4).",
    )
    slot_number: Optional[int] = None
    registered_at: datetime = Field(default_factory=_utcnow)
    confirmed: bool = False


class Registration(BaseModel):
    """A pending / completed / expired slot claim.  Admin-deletable."""
    guild_id: int
    panel_id: str
    window: str
    claimer_discord_id: int
    claimed_at: datetime = Field(default_factory=_utcnow)
    claim_deadline: datetime
    status: RegistrationStatus = RegistrationStatus.PENDING
    team_name: Optional[str] = None


class Ban(BaseModel):
    """Player ban.  Permanent collection; optional auto-expiry via TTL."""
    discord_id: int
    guild_id: int
    reason: Optional[str] = None
    banned_by: int
    banned_at: datetime = Field(default_factory=_utcnow)
    expires_at: Optional[datetime] = None


class Verification(BaseModel):
    """Screenshot submission + review.  TTL 7 days post-verification."""
    team_name: str
    guild_id: int
    panel_id: str
    window: str
    screenshot_urls: List[str] = Field(default_factory=list)
    submitted_by: int
    submitted_at: datetime = Field(default_factory=_utcnow)
    status: VerificationStatus = VerificationStatus.PENDING
    reviewed_by: Optional[int] = None
    reviewed_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None


class PointEntry(BaseModel):
    """Points for one team in one match.  Permanent."""
    team_name: str
    guild_id: int
    panel_id: str
    window: str
    match_number: int = 1
    kills: int = 0
    placement: int = 0
    total_points: float = 0.0
    added_by: int
    added_at: datetime = Field(default_factory=_utcnow)


class Group(BaseModel):
    """A lobby / group with its own channels.  Permanent."""
    guild_id: int
    group_number: int
    channel_ids: Dict[str, int] = Field(
        default_factory=dict,
        description="e.g. {'text': 123, 'voice': 456}",
    )
    created_at: datetime = Field(default_factory=_utcnow)


class RecentlyDeleted(BaseModel):
    """Soft-delete snapshot.  TTL index on expires_at (5 min)."""
    original_collection: str
    original_id: str
    snapshot: Dict[str, Any]
    deleted_by: int
    deleted_at: datetime = Field(default_factory=_utcnow)
    expires_at: datetime


class LogEntry(BaseModel):
    """Audit-style log.  Admin-deletable."""
    guild_id: int
    action: str
    actor_discord_id: int
    details: Dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=_utcnow)


class SharedChannel(BaseModel):
    """Guild-level shared channels (verify-teamname, conf, banlist, etc.)."""
    guild_id: int
    verify_teamname_channel_id: Optional[int] = None
    conf_channel_id: Optional[int] = None
    banlist_channel_id: Optional[int] = None
    leaderboard_channel_id: Optional[int] = None
    admin_channel_id: Optional[int] = None
