# ⚔️ Hybrid Scrim Bot

A Discord bot + web dashboard for managing BGMI scrims, built with **discord.py**, **FastAPI**, and **MongoDB Atlas**.

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

## Deployment (Render)

Create two services from the same repo:

| Service | Type | Start Command |
|---------|------|---------------|
| `scrimbot` | Worker | `python -m bot.main` |
| `scrimbot-dashboard` | Web Service | `uvicorn dashboard.main:app --host 0.0.0.0 --port $PORT` |

Set all `.env` variables as Render environment variables on both services.

## Bot Commands

### Admin Commands
| Command | Description |
|---------|-------------|
| `/panel create <id> <window>` | Create a new scrim panel with channels/role |
| `/panel settings <id>` | Edit panel configuration (match start, slots, etc.) |
| `/panel channels <id>` | Rename panel channels (with cooldown) |
| `/setup_linkid` | Post the Link-Your-ID button |
| `/set_groups <count>` | Create/archive lobby groups |
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

### Player Actions
- **Claim Slot** — Button on registration embed
- **Link Your ID** — Button in #verify-teamname
- **Cancel Slot** — Button on slot board
- **Transfer Slot** — Button on slot board

## Dashboard Pages

| Page | Features |
|------|----------|
| Overview | Guild stats, active panels, recent activity |
| Teams | Browse/search teams, filter by panel |
| Groups | View group channels |
| Points | Visual podium leaderboard, per-panel |
| Verifications | Screenshot review queue with status filter |
| Bans | Add/remove bans, view history |
| Channels | View all channel IDs |
| Settings | Destructive data controls (typed confirmation) |

## Key Features

- **Idempotent channel provisioning** — Never creates duplicates
- **Rename cooldown enforcement** — Self-limits to 2 renames/10 min per channel
- **Cancel lock** — Blocks cancellations within X minutes of match start
- **Duplicate player check** — Indexed query prevents same player on multiple teams
- **Claim timeout** — Auto-releases unclaimed slots (survives restarts)
- **Soft-delete with undo** — 5-minute restore window via TTL
- **Auto-expiry bans** — MongoDB TTL index
- **Discord OAuth2** — Admin-only dashboard access

## Project Structure

```
new working/
├── shared/           # Pydantic models, DB utilities, config
├── bot/
│   ├── main.py       # Entry point
│   ├── cogs/         # All slash command cogs
│   ├── views/        # Persistent views + modals
│   ├── tasks/        # Background tasks
│   └── utils/        # Channel ops, checks, cooldown
├── dashboard/
│   ├── main.py       # FastAPI entry
│   ├── auth.py       # Discord OAuth2
│   ├── routers/      # Page routers
│   ├── templates/    # Jinja2 HTML
│   └── static/       # CSS + JS
├── requirements.txt
├── Procfile
└── .env.example
```
