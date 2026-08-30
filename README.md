# ⚔️ Hybrid Scrim Bot

A Discord bot + web dashboard for managing BGMI scrims, built with **discord.py**, **FastAPI**, and **MongoDB Atlas**.

---

## Architecture

```
┌──────────────┐     ┌──────────────┐
│  Discord Bot │     │   FastAPI    │
│  (Worker)    │────▶│  Dashboard   │
└──────┬───────┘     └──────┬───────┘
       │                     │
       ▼                     ▼
  ┌────────────────────────────┐
  │     MongoDB Atlas          │
  │  (shared database)         │
  └────────────────────────────┘
```

Two separate processes sharing the same `shared/` layer (models, database, config).

---

## ✨ Key Features

### 🎮 Multi-Group Registration System
- **Per-group slot numbering** — Slots are prefixed by group (e.g. `G01-03`, `G02-07`) for clear lobby assignment
- **Dynamic segmented progress bar** — Pillow-generated image updates in real time after each registration
- **Thread-safe image generation** — Pillow renders offloaded to `asyncio.to_thread()` to prevent event loop blocking
- **Per-group capacity tracking** — Each group shows its own fill status: `` `G01`: 8/10 ┃ `G02`: 6/10 ``
- **Multi-group registration toggle** — Admin-configurable: allow players to register in multiple groups, or restrict to one

### 🛡️ Admin Control Panel (`#T1-admin`)
- **👥 Groups** — Configure number of lobby groups
- **⏰ Schedule** — Bulk schedule registration open/close times
- **🎯 Slots** — Set per-group slot capacity
- **🚀 Post to Reg Portal** — Provision/update the registration embed
- **📋 Send Slot Lists** — Dispatch team rosters to all lobby channels
- **📸 Open/Close SS Window** — Manual override for screenshot submission
- **⚙️ Midnight Reset** — Configure automated daily reset options
- **⚡ Instant Reset** — Immediately wipe a panel's data

### 📸 Screenshot Approval System
- **30-minute submission window** — Auto-opens after match end, auto-closes after 30 minutes
- **IDP role-based channel locks** — Lobby channels unlock for the IDP role only (not `@everyone`)
- **Private admin review threads** — Each screenshot gets a dedicated private thread in `#T1-group-X`, invisible to players
- **Dual approval flow** — Interactive buttons (`✅ Approve SS` / `❌ Reject SS`) + text commands (`!approve` / `!reject`)
- **One-by-one review** — Each submission has its own isolated approval workflow

### 🔄 Slot Management (`#T1-slotmng`)
- **🔀 Choose Lobby** — Switch between groups
- **❌ Cancel Slot** — Cancel with automatic waitlist notification
- **🔄 Transfer Slot** — Admin-controlled atomic slot reassignment
- **🔔 Reminders** — Subscribe to be pinged when a slot opens up
- **👥 Role Transfer** — Pass slot role to a teammate

### 🌙 Midnight Auto-Reset
- **Runs at 00:00 IST daily** — Configurable per-panel
- **Selective reset options:**
  - Purge messages (keeps pinned)
  - Clear teams & registrations
  - Revoke IDP / tag roles (rate-limited)
  - Reset progress bars & SS window status
- **Admin override** — Choose which panels to include/exclude

### 🏆 Rate-Limited Team Promotions
- **`/promote_teams`** — Promote top 3 teams with role assignments
- **`asyncio.sleep(1.0)` between each role** — Prevents Discord 429 rate limit errors when assigning roles to 12+ players

### 🔒 Technical Safeguards
- **Atomic MongoDB operations** — Slot cancellations/claims use `$set`/`$unset` directly, no full-document round-trips
- **CPU-bound Pillow protection** — Image generation never blocks the bot's event loop
- **Duplicate player detection** — Indexed compound query prevents the same player on multiple teams
- **Cancel lock window** — Blocks cancellations within X minutes of match start
- **Claim timeout** — Auto-releases unclaimed slots (survives bot restarts)
- **Soft-delete with undo** — 5-minute restore window via MongoDB TTL
- **Auto-expiry bans** — MongoDB TTL index handles ban expiration
- **Persistent views** — All buttons survive bot restarts via stable `custom_id` patterns

---

## Setup

### 1. Prerequisites
- Python 3.11+
- MongoDB Atlas cluster
- Discord bot application (with OAuth2 configured)

### 2. Install dependencies
```bash
pip install -r requirements.txt
```

### 3. Configure environment
```bash
cp .env.example .env
# Edit .env with your secrets
```

### 4. Run locally
```bash
# Bot (in one terminal)
python -m bot.main

# Dashboard (in another terminal)
uvicorn dashboard.main:app --reload --port 8000
```

---

## Deployment (Render)

Create two services from the same repo:

| Service | Type | Start Command |
|---------|------|---------------|
| `scrimbot` | Worker | `python -m bot.main` |
| `scrimbot-dashboard` | Web Service | `uvicorn dashboard.main:app --host 0.0.0.0 --port $PORT` |

Set all `.env` variables as Render environment variables on both services.

---

## Bot Commands

### Slash Commands (Admin)
| Command | Description |
|---------|-------------|
| `/panel create <id> <window>` | Create a new scrim panel with channels/role |
| `/panel settings <id>` | Edit panel configuration (match start, slots, etc.) |
| `/panel channels <id>` | Rename panel channels (with cooldown) |
| `/panel reset <id>` | Manually reset a panel |
| `/setup_linkid` | Post the Link-Your-ID button |
| `/set_groups <count>` | Create/archive lobby groups |
| `/promote_teams <panel>` | Promote top 3 teams (rate-limited role assignment) |
| `/add_points <panel> <team> <kills> <placement>` | Add match points |
| `/pointtable <panel>` | Show standings |
| `/postpointtable <panel>` | Post leaderboard to #leaderboard |
| `/ban <user> [duration] [reason]` | Ban a player |
| `/unban <user>` | Remove a ban |
| `/remove_team <panel> <team>` | Remove team (bypasses cancel lock) |
| `/clear_registration <panel> <user>` | Clear a player's registration |
| `/faketag <panel> <team> [reason]` | Flag team for review |
| `/delete_message <id>` | Soft-delete (5-min undo) |
| `/undo <snapshot_id>` | Restore deleted message |
| `/slotboard <panel>` | Force-refresh slot board |

### Prefix Commands
| Command | Description |
|---------|-------------|
| `!approve <team>` | Approve screenshot verification |
| `!reject <team> [reason]` | Reject screenshot verification |

### Player Actions (Buttons)
| Button | Location | Description |
|--------|----------|-------------|
| 📥 Register G01/G02... | `#T1-reg` | Claim a slot in a specific group |
| 🔗 Link Your ID | `#verify` | Link/update BGMI ID |
| ❌ Cancel Slot | `#T1-slotmng` | Cancel your slot (pings waitlist) |
| 🔔 Reminders | `#T1-slotmng` | Subscribe to slot-open notifications |
| 🔄 Transfer Slot | `#T1-slotmng` | Admin-controlled slot reassignment |

---

## Discord Channel Layout

When a panel `T1` is created with 2 groups, the bot provisions:

```
📁 T1-SCRIMS
├── #T1-admin          ← Admin Control Panel (ACP) with interactive buttons
├── #T1-reg-8PM        ← Registration portal with group buttons + progress bar
├── #T1-tag            ← Tag submission channel (auto-granted access on claim)
├── #T1-conf           ← Registration confirmations with admin action buttons
├── #T1-slotmng        ← Slot management hub (cancel, transfer, reminders)
├── #T1-winner         ← Winner announcements & leaderboard
├── #T1-group-01       ← Lobby channel for Group 01 (SS submission + private review threads)
└── #T1-group-02       ← Lobby channel for Group 02
```

---

## Dashboard Pages

| Page | Features |
|------|----------|
| Overview | Guild stats, active panels, recent activity |
| Teams | Browse/search teams, filter by panel |
| Groups | View group channels and rosters |
| Points | Visual podium leaderboard, per-panel |
| Verifications | Screenshot review queue with status filter |
| Bans | Add/remove bans, view history |
| Channels | View all channel IDs |
| Settings | Destructive data controls (typed confirmation) |

---

## Project Structure

```
Hybrid-Scrim-Bot/
├── shared/                # Pydantic models, DB utilities, config
│   ├── models.py          # PanelConfig, Team, Registration, Verification, etc.
│   ├── database.py        # Motor client, collection accessors, indexes
│   └── config.py          # Environment variables
├── bot/
│   ├── main.py            # Entry point, view registration, task lifecycle
│   ├── cogs/
│   │   ├── panel.py       # Multi-group provisioning, ACP, promotions
│   │   ├── registration.py # Tag submission, group routing, progress bar
│   │   ├── screenshots.py # SS submission, private threads, dual approval
│   │   ├── slotboard.py   # Live slot board embed
│   │   ├── points.py      # Points management & leaderboard
│   │   ├── groups.py      # Group management
│   │   ├── link_id.py     # BGMI ID linking
│   │   ├── moderation.py  # Bans, faketag, delete/undo
│   │   └── undo.py        # Message restore
│   ├── views/
│   │   ├── persistent.py  # All persistent views (AdminControlPanel, Registration, SlotMgmt, SS Approval)
│   │   ├── modals.py      # All modals (Groups, Schedule, Slots, Reset, Reject)
│   │   └── slot_views.py  # Slot board view
│   ├── tasks/
│   │   ├── scheduler.py   # APScheduler for reg open/close
│   │   ├── claim_timeout.py # Auto-release unclaimed slots
│   │   ├── screenshot_window.py # Auto open/close 30-min SS window
│   │   └── midnight_reset.py   # Daily midnight selective reset
│   └── utils/
│       ├── channel_ops.py # Idempotent channel/role provisioning
│       ├── checks.py      # Ban checks, admin checks
│       ├── cooldown.py    # Rename cooldown tracking
│       └── progress_bar.py # Pillow segmented bar (thread-safe)
├── dashboard/
│   ├── main.py            # FastAPI entry
│   ├── auth.py            # Discord OAuth2
│   ├── routers/           # Page routers
│   ├── templates/         # Jinja2 HTML
│   └── static/            # CSS + JS
├── requirements.txt
├── Procfile
└── .env.example
```

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Bot Framework | discord.py 2.3+ |
| Database | MongoDB Atlas (Motor async driver) |
| Dashboard | FastAPI + Jinja2 |
| Image Generation | Pillow (thread-offloaded) |
| Scheduling | APScheduler |
| Models | Pydantic v2 |
| Hosting | Render (Worker + Web Service) |

---

## License

This project is private. All rights reserved.
