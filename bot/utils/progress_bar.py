"""
Segmented progress bar generator using Pillow.

Safe against event loop blocking by offloading synchronous image
drawing to a thread pool via asyncio.to_thread().
"""

from __future__ import annotations

import asyncio
import io
import discord
from PIL import Image, ImageDraw, ImageFont


def generate_segmented_bar_sync(
    filled: int,
    total: int,
    width: int = 440,
    height: int = 40,
    segment_gap: int = 4,
) -> io.BytesIO:
    """
    Synchronously draw a segmented progress bar.
    Must be called via asyncio.to_thread() to avoid blocking the event loop.
    """
    total = max(1, total)
    filled = max(0, min(filled, total))

    # Background canvas (Dark sleek background)
    img = Image.new("RGBA", (width, height), (22, 24, 29, 255))
    draw = ImageDraw.Draw(img)

    # Segment calculations
    available_width = width - 16  # 8px padding each side
    total_gaps = (total - 1) * segment_gap
    seg_width = max(2.0, (available_width - total_gaps) / total)

    start_x = 8.0
    start_y = 8.0
    seg_height = height - 16.0

    # Color palette
    filled_color = (88, 101, 242, 255)       # Discord Blurple / Neon Cyan (79, 84, 92)
    if filled >= total:
        filled_color = (235, 69, 158, 255)   # Hot Pink / Red when full
    elif filled / total >= 0.75:
        filled_color = (250, 166, 26, 255)   # Amber Warning
    else:
        filled_color = (67, 181, 129, 255)   # Emerald Green

    empty_color = (47, 49, 54, 255)          # Dark grey

    for i in range(total):
        x0 = start_x + i * (seg_width + segment_gap)
        x1 = x0 + seg_width
        y0 = start_y
        y1 = start_y + seg_height
        color = filled_color if i < filled else empty_color
        draw.rounded_rectangle([x0, y0, x1, y1], radius=3, fill=color)

    # Export to in-memory bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


async def generate_segmented_bar(
    filled: int, total: int, filename: str = "progress.png"
) -> discord.File:
    """
    Async wrapper that safely offloads Pillow generation to a worker thread.
    """
    buf = await asyncio.to_thread(generate_segmented_bar_sync, filled, total)
    return discord.File(fp=buf, filename=filename)


def make_circle_bar(
    filled: int,
    total: int,
    bar_len: int = 10,
    filled_char: str = "●",
    empty_char: str = "o",
) -> str:
    """
    Build a clean 10-dot circle bar (e.g. ●●●oooooooo) scaled to capacity.
    Fixed 10-dot length ensures it never line-wraps on mobile screens.
    Uses 'o' for empty slots to create small, crisp hollow circles matching Tortuga style.
    """
    total = max(1, total)
    filled = max(0, min(filled, total))

    filled_count = int(round((filled / total) * bar_len))
    filled_count = min(max(filled_count, 0), bar_len)
    empty_count = bar_len - filled_count

    return filled_char * filled_count + empty_char * empty_count


def render_registration_embed(
    panel_id: str,
    window: str,
    group_count: int,
    schedules: list[dict],
    group_fill_counts: dict[str, int],
    max_slots: int = 20,
) -> discord.Embed:
    """Build the clean Tortuga/Mack-style Scrims Slot Availability Embed matching Image 2."""
    from datetime import datetime, timezone

    upper = panel_id.upper()
    tier_label = f"TIER {panel_id[1:]}" if (panel_id.upper().startswith("T") and panel_id[1:].isdigit()) else upper

    lines = []

    # If schedules provided, iterate over open schedules; else fallback to 1..group_count
    if schedules:
        target_schedules = [s for s in schedules if s.get("status", "open") == "open"]
    else:
        target_schedules = [{"group_id": f"G{i:02d}"} for i in range(1, group_count + 1)]

    for grp_sched in target_schedules:
        gid = grp_sched.get("group_id", "G01")
        # Format display group name nicely (e.g. "Group 1839" or "Group G01" or "Group 1")
        group_display = gid if gid.lower().startswith("group") else f"Group {gid}"

        cap = grp_sched.get("capacity", max_slots)
        res_count = grp_sched.get("reserved_slots", 0)
        public_cap = max(1, cap - res_count)
        filled = group_fill_counts.get(gid, 0)

        # Status emoji indicator
        if filled >= public_cap:
            status_icon = "🔴"
        elif (filled / public_cap) >= 0.75:
            status_icon = "🟡"
        else:
            status_icon = "🟢"

        # 1. Group Header (e.g. 🟢 Group 1839 — 1st SEPT or 🟢 Group G01 — 8PM)
        header = f"{status_icon} **{group_display} — {window}**"

        # 2. Time line (IDP times preferred, match times as fallback)
        m1_idp = grp_sched.get("m1_idp_time")
        m2_idp = grp_sched.get("m2_idp_time")
        m1_time = grp_sched.get("m1_time", "12:00 PM")
        m2_time = grp_sched.get("m2_time", "12:45 PM")

        if m1_idp and m2_idp:
            time_line = f"⌚ **IDP:** M1: `{m1_idp}` | M2: `{m2_idp}`"
        elif m1_time and m2_time:
            time_line = f"⌚ **IDP:** M1: `{m1_time}` | M2: `{m2_time}`"
        else:
            time_line = "⌚ **IDP:** M1: `TBD` | M2: `TBD`"

        # 3. 10-dot Progress Bar line in rounded code box (`●●●oooooooo` 1/16 filled • 4 Reserved)
        bar = make_circle_bar(filled, public_cap, bar_len=10, filled_char="●", empty_char="o")
        res_text = f" • `{res_count} Reserved`" if res_count > 0 else ""
        fill_status = f"{filled}/{public_cap} filled{res_text}" if filled < public_cap else f"{filled}/{public_cap} (FULL){res_text}"
        bar_line = f"`{bar}` {fill_status}"

        lines.append(f"{header}\n{time_line}\n{bar_line}")

    embed = discord.Embed(
        title=f"🦅 {tier_label} SCRIMS — Slot availability",
        description="\n\n".join(lines) if lines else "*No groups configured.*",
        colour=discord.Colour.from_rgb(230, 100, 30),  # Tortuga Orange Strip
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Auto-updates on every registration event • {tier_label} SCRIMS")
    return embed

