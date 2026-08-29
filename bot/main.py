"""
Bot entry point — startup, cog loading, persistent view registration.

Run with:  python -m bot.main
"""

from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import discord
from discord.ext import commands

from shared.config import DISCORD_BOT_TOKEN, GUILD_ID
from shared.database import close as db_close, ensure_indexes, panels_col

# Views must be imported so their classes are available for add_view()
from bot.views.persistent import AdminActionsView, ClaimSlotView, LinkIDView, PanelControlView
from bot.views.slot_views import SlotBoardView
from bot.tasks.claim_timeout import ClaimTimeoutTask
from bot.tasks.scheduler import scrim_scheduler, trigger_open, trigger_close
from bot import keep_alive

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("scrimbot")

# ── Intents ────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.messages = True
intents.message_content = True

# ── Bot instance ───────────────────────────────────────────────────────────
bot = commands.Bot(
    command_prefix="!",
    intents=intents,
    help_command=None,
)

# Background tasks holder
claim_timeout_task: ClaimTimeoutTask | None = None


# ── Schedule recovery ─────────────────────────────────────────────────────
async def recover_schedules() -> None:
    """Re-schedule APScheduler jobs that were lost during a bot restart."""
    now_utc = datetime.now(ZoneInfo("UTC"))

    cursor = panels_col().find({
        "status": {"$in": ["scheduled", "open"]},
        "schedule_close": {"$gt": now_utc},
    })

    count = 0
    async for panel in cursor:
        panel_id = panel["panel_id"]
        ch_ids = panel.get("channel_ids") or {}
        channel_id = ch_ids.get("reg_channel_id")
        guild_id = panel["guild_id"]

        if not channel_id:
            continue

        # Re-schedule open job if it hasn't fired yet
        if (
            panel["status"] == "scheduled"
            and panel.get("schedule_open")
            and panel["schedule_open"] > now_utc
        ):
            scrim_scheduler.add_job(
                trigger_open, "date", run_date=panel["schedule_open"],
                args=[bot, channel_id, panel_id, guild_id],
                id=f"open_{panel_id}", replace_existing=True,
            )

        # Always re-schedule the close job (its time is guaranteed > now)
        scrim_scheduler.add_job(
            trigger_close, "date", run_date=panel["schedule_close"],
            args=[bot, channel_id, panel_id, guild_id],
            id=f"close_{panel_id}", replace_existing=True,
        )
        count += 1

    scrim_scheduler.start()
    log.info("APScheduler started — recovered %d schedule(s).", count)


# ── Cog list ───────────────────────────────────────────────────────────────
COG_EXTENSIONS = [
    "bot.cogs.panel",
    "bot.cogs.registration",
    "bot.cogs.slotboard",
    "bot.cogs.link_id",
    "bot.cogs.screenshots",
    "bot.cogs.points",
    "bot.cogs.moderation",
    "bot.cogs.groups",
    "bot.cogs.undo",
]


@bot.event
async def on_ready() -> None:
    global claim_timeout_task

    log.info("Logged in as %s (ID: %s)", bot.user, bot.user.id)

    # 1. Database indexes
    await ensure_indexes()

    # 2. Register persistent views
    bot.add_view(LinkIDView())
    bot.add_view(ClaimSlotView())
    bot.add_view(SlotBoardView())
    bot.add_view(AdminActionsView())

    # 2b. Register PanelControlView for every existing panel
    async for panel in panels_col().find({}, {"panel_id": 1}):
        bot.add_view(PanelControlView(bot, panel["panel_id"]))

    # 3. Load cogs
    for ext in COG_EXTENSIONS:
        try:
            await bot.load_extension(ext)
            log.info("Loaded cog: %s", ext)
        except commands.ExtensionAlreadyLoaded:
            pass
        except Exception:
            log.exception("Failed to load cog: %s", ext)

    # 4. Sync slash commands to the target guild
    if GUILD_ID:
        guild_obj = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild_obj)
        await bot.tree.sync(guild=guild_obj)
        log.info("Synced slash commands to guild %s", GUILD_ID)
    else:
        await bot.tree.sync()
        log.info("Synced slash commands globally")

    # 5. Start background tasks
    claim_timeout_task = ClaimTimeoutTask(bot)
    claim_timeout_task.start()

    # 6. Recover scheduled registration jobs and start APScheduler
    await recover_schedules()

    log.info("Bot is ready.")


@bot.event
async def on_close() -> None:
    if claim_timeout_task:
        claim_timeout_task.stop()
    if scrim_scheduler.running:
        scrim_scheduler.shutdown(wait=False)
    await db_close()


# ── Run ────────────────────────────────────────────────────────────────────
def main() -> None:
    if not DISCORD_BOT_TOKEN:
        log.error("DISCORD_BOT_TOKEN is not set — aborting.")
        sys.exit(1)

    # Start keep-alive HTTP server for Render (daemon thread, non-blocking)
    keep_alive.start()

    bot.run(DISCORD_BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()

