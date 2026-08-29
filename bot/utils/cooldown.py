"""
Rename-cooldown enforcement.

Discord rate-limits channel/role renames to roughly 2 per 10 minutes
per resource.  We self-enforce this limit by tracking timestamps in
the panel document so we NEVER send an API call that would 429.
"""

from __future__ import annotations

import time
from typing import Tuple

from shared.config import MAX_RENAMES_PER_COOLDOWN, RENAME_COOLDOWN_SECONDS
from shared.database import panels_col


async def check_rename_allowed(
    guild_id: int,
    panel_id: str,
    resource_id: int,
) -> Tuple[bool, float]:
    """Check whether a rename is allowed for *resource_id* (channel or role).

    Returns ``(allowed, remaining_seconds)``.  If ``allowed`` is False,
    ``remaining_seconds`` indicates how long the caller must wait.
    """
    key = str(resource_id)
    panel = await panels_col().find_one(
        {"guild_id": guild_id, "panel_id": panel_id}
    )
    if panel is None:
        return True, 0.0

    history: list[float] = panel.get("rename_history", {}).get(key, [])
    now = time.time()
    cutoff = now - RENAME_COOLDOWN_SECONDS

    # Only keep timestamps within the cooldown window
    recent = [ts for ts in history if ts > cutoff]

    if len(recent) >= MAX_RENAMES_PER_COOLDOWN:
        oldest_in_window = min(recent)
        remaining = (oldest_in_window + RENAME_COOLDOWN_SECONDS) - now
        return False, max(remaining, 0.0)

    return True, 0.0


async def record_rename(
    guild_id: int,
    panel_id: str,
    resource_id: int,
) -> None:
    """Record a successful rename timestamp for *resource_id*."""
    key = str(resource_id)
    now = time.time()
    cutoff = now - RENAME_COOLDOWN_SECONDS

    # Atomically push the new timestamp and prune old ones.
    # We store only recent timestamps to keep the document lean.
    panel = await panels_col().find_one(
        {"guild_id": guild_id, "panel_id": panel_id}
    )
    if panel is None:
        return

    history: list[float] = panel.get("rename_history", {}).get(key, [])
    history = [ts for ts in history if ts > cutoff]
    history.append(now)

    await panels_col().update_one(
        {"guild_id": guild_id, "panel_id": panel_id},
        {"$set": {f"rename_history.{key}": history}},
    )
