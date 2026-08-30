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
