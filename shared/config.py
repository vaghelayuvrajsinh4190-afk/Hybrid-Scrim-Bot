"""
Configuration — environment variable loading and application constants.

All secrets and deployment-specific values are loaded from environment
variables (with .env fallback for local dev).  Constants are the single
source of truth for default values referenced across the bot and dashboard.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Load .env from repo root (two levels up from shared/)
# ---------------------------------------------------------------------------
_env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

# ---------------------------------------------------------------------------
# Required secrets
# ---------------------------------------------------------------------------
DISCORD_BOT_TOKEN: str = os.environ.get("DISCORD_BOT_TOKEN", "")
MONGODB_URI: str = os.environ.get("MONGODB_URI", "")
GUILD_ID: int = int(os.environ.get("GUILD_ID", "0"))

# Dashboard-specific
DISCORD_CLIENT_ID: str = os.environ.get("DISCORD_CLIENT_ID", "")
DISCORD_CLIENT_SECRET: str = os.environ.get("DISCORD_CLIENT_SECRET", "")
DASHBOARD_SECRET_KEY: str = os.environ.get("DASHBOARD_SECRET_KEY", "change-me")
DASHBOARD_URL: str = os.environ.get("DASHBOARD_URL", "http://localhost:8000")

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DB_NAME: str = os.environ.get("DB_NAME", "scrimbot")

# ---------------------------------------------------------------------------
# Default tuning knobs (all admin-overridable per panel)
# ---------------------------------------------------------------------------
DEFAULT_CANCEL_LOCK_MINUTES: int = 60
DEFAULT_CLAIM_TIMEOUT_MINUTES: int = 5
DEFAULT_MAX_SLOTS: int = 20
DEFAULT_SCREENSHOT_WINDOW_MINUTES: int = 30

# ---------------------------------------------------------------------------
# Rename-cooldown enforcement
# Discord allows ~2 channel/role name edits per 10 minutes before 429-ing.
# We self-enforce this limit so we never send a request that would be rejected.
# ---------------------------------------------------------------------------
RENAME_COOLDOWN_SECONDS: int = 600          # 10 minutes
MAX_RENAMES_PER_COOLDOWN: int = 2

# ---------------------------------------------------------------------------
# Data-retention timers
# ---------------------------------------------------------------------------
RECENTLY_DELETED_TTL_SECONDS: int = 300     # 5 minutes
VERIFICATION_TTL_DAYS: int = 7              # 7 days post-review

# ---------------------------------------------------------------------------
# Claim-timeout checker interval
# ---------------------------------------------------------------------------
CLAIM_CHECK_INTERVAL_SECONDS: int = 30
