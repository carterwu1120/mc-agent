# Minecraft LLM Agent

An autonomous Minecraft bot powered by a large language model (Gemini / Ollama), capable of planning multi-step tasks, recovering from failures, managing inventory, and operating as part of a multi-agent system — all without human intervention after the initial instruction.

> **Side project** | Node.js · Python · mineflayer · aiohttp · Docker

---

## Demo

```
Player: @Agent0 mine diamond 10
Agent0: 好，我會先準備工具和食物，再開始挖鑽石。
→ [hunts food] → [smelts food] → [crafts iron pickaxe] → [mines diamonds]
→ (stuck: no animals nearby) → LLM replans → [explores new area] → [resumes]
```

The bot autonomously decomposes a high-level goal into a sequence of steps, executes them in order, and recovers from failures — without any hardcoded decision trees.

---

## Architecture

### Service Design

```
┌─────────────────────────────────────────────────────┐
│  Client / Dashboard UI                              │
│  Browser dashboard, curl, future external tooling   │
└─────────────────┬───────────────────────────────────┘
                  │ HTTP :8000
                  │ public JSON API
┌─────────────────▼───────────────────────────────────┐
│  FastAPI Backend                                    │
│  Public control/query facade                        │
│                                                     │
│  GET  /health                                       │
│  GET  /ready                                        │
│  POST /bots/{id}/tasks                              │
│  GET  /bots                                         │
│  GET  /bots/{id}/state                              │
│  GET  /tasks/{id}                                   │
└─────────────────┬───────────────────────────────────┘
                  │ internal HTTP + shared data
┌─────────────────▼───────────────────────────────────┐
│  Python Agent + Coordinator (bot0)                 │
│  LLM planning, task execution, multi-bot queueing   │
│                                                     │
│  Dashboard page server (:3002, UI only)            │
│  Coordinator service (:3010, internal only)        │
└─────────────────┬───────────────────────────────────┘
                  │ WebSocket :3001
                  │ JSON events + commands
┌─────────────────▼───────────────────────────────────┐
│  JS Bot (Node.js + mineflayer)                      │
│  Real-time game control: movement, digging,         │
│  fishing, crafting, inventory management            │
└─────────────────────────────────────────────────────┘
```

**Why this split?**
- JS (mineflayer) has the best ecosystem for real-time Minecraft control
- Python has the best ecosystem for LLM integration, async orchestration, and data persistence
- Coordinator decouples multi-bot task assignment from individual bot logic — bots only need to know the coordinator's URL, not each other's addresses
- Backend provides one public API instead of exposing dashboard and coordinator query surfaces directly
- Clear boundaries: JS reports what happened → Python decides what to do → Coordinator distributes work across bots → Backend exposes stable APIs

### Example: Player says "mine 10 diamonds"

```
Player types in Minecraft chat
  │
  │  [game protocol]
  ▼
JS bot receives chat event
  │
  │  [WebSocket]  sendState(bot, 'chat', { message: '...' })
  ▼
Python agent — routes to planner.py
  │
  │  [HTTPS]  POST api.google.com/gemini
  ▼
LLM returns plan: ["equip pickaxe", "mine diamond 10"]
  │
  │  [WebSocket]  { command: "equip" }
  ▼
JS bot equips pickaxe → done
  │
  │  [WebSocket]  { type: "action_done" }
  ▼
Python agent sends next step
  │
  │  [WebSocket]  { command: "mine diamond 10" }
  ▼
JS bot mining... gets stuck
  │
  │  [WebSocket]  { type: "activity_stuck", reason: "no_progress" }
  ▼
Python agent — routes to activity_stuck.py → asks LLM for recovery
  │
  │  [WebSocket]  { command: "explore" }  ← LLM decides to explore first
  ▼
JS bot explores, resumes mining, finishes
  │
  │  [WebSocket]  { type: "activity_done" }
  ▼
Python agent marks task complete
```

**Communication summary:**
| Transport | Used for |
|-----------|---------|
| WebSocket | All JS bot ↔ Python agent messages (events and commands) |
| HTTPS | Python agent → Gemini API (LLM calls) |
| HTTP :3010 | Python agent ↔ Coordinator (internal multi-bot task assignment only) |
| HTTP :8000 | Client / dashboard UI ↔ Backend public API |

---

## Key Technical Components

### 1. LIFO Activity Stack

The JS bot manages activities (mining, fishing, smelting…) as a **Last-In First-Out stack**. Pushing a new activity automatically pauses the current one; popping automatically resumes it. This enables clean task preemption without state corruption.

```
Stack: [mining] → inventory full → push [handling_inventory] → pop → resume [mining]
```

Each stack frame stores: activity name, goal, progress, start position, and a resume function. The Python agent never needs to know the internal mechanics — it just sends commands.

### 2. Layered Decision Making

Not everything goes to the LLM. Decisions are layered:

```
Event arrives
  │
  ├─ Deterministic shortcut?  ──────────────────→ Execute immediately
  │  (food low, tool broken, known pattern)
  │
  ├─ System-layer rules (pre/post LLM)
  │  _enforce_pending_steps()
  │  _block_invalid_skip()
  │  _deduplicate_adjacent_cmds()
  │
  └─ LLM decision
       ↓
     Response validated and filtered by rules
```

This approach keeps LLM costs low and prevents the model from making structurally invalid decisions (e.g., skipping a step that a later step depends on).

### 3. PlanExecutor

When the LLM returns a multi-step plan, `PlanExecutor` sequences the commands, waiting for `action_done` / `activity_done` signals between each step. It supports:

- **Context substitution**: `{new_chest_id}` is filled in at runtime after `makechest` completes
- **Per-step verification** (`_verify_step`): compares before/after inventory after each equip / smelt / mine / deposit / fish / hunt — if the action didn't take effect, routes back to the LLM for recovery
- **Goal-level verification** (`_verify_goal`): after *all* steps complete, compares inventory at plan-start vs. plan-end against the full target count of the last output command. Stricter than per-step: catches cases where individual steps appeared to succeed but the cumulative result fell short
- **Deterministic remediation** (`_build_goal_remediation`): if goal verification fails, computes the deficit and emits the minimum fix (e.g. `smelt raw_iron 8` short by 3 with 1 in inventory → `['mine iron 2', 'smelt raw_iron 3']`). Retries once; further shortfalls are recorded as `goalVerified: false` for `self_task` to pick up
- **Replan during execution**: the LLM can replace remaining steps mid-plan via `{"action": "replan", ...}`
- **Step skip / abort**: granular control without losing the overall task context
- **Dropped-command detection**: if a sent activity command never starts (JS stays idle for 90s), automatically retransmits once

### 4. Working Memory (`task_memory`)

`task.json` functions as the bot's short-term working memory:

```json
{
  "goal": "mine diamond 10",
  "final_goal": "mine diamond 10",
  "steps": [...],
  "currentStep": 2,
  "status": "running",
  "goalVerified": true,        // set when task completes: true = goal confirmed, false = fell short
  "interruptedTasks": [...],   // up to 3 paused tasks with full context
  "recentEvents": [...],       // replans, skips, aborts — with timestamps
  "recentFailures": [...]      // per-command failure log
}
```

The `final_goal` field persists the player's overarching intent across task replacements — so even after detours (hunt food → craft tools → mine iron), the bot remembers it was ultimately asked to mine diamonds.

Memory is pruned by TTL and item cap to prevent context bloat on long-running sessions.

### 5. Stuck Recovery

Three-layer recovery model:

| Layer | Who handles it | Example |
|-------|---------------|---------|
| Mechanical | JS watchdog | Bot is stuck against a wall → pathfind around |
| Orchestration | Python executor | Plan step timed out → replan or skip |
| Strategic | LLM via `activity_stuck` | No animals found → decide whether to explore or switch strategy |

The LLM is only called for strategic decisions. Mechanical issues are resolved in JS without ever involving Python.

### 6. Observability Dashboard

A live HTTP dashboard page is served on port `3002`, and it now reads data from the backend public API on port `8000`.

The UI shows:

- Health / food bars, position, activity
- Current task with step-by-step progress bar
- Equipment durability, inventory, chest contents
- Recent events and failure log
- Coordinator runtime state (queue depth, running task ids, abort flag)

The dashboard server is now **UI-only**:
- `GET /` serves the HTML page
- data comes from backend APIs such as `GET /bots` and `GET /bots/{id}/state`
- old dashboard query routes are no longer the public API surface

---

## Multi-Agent Support

Multiple bots can run simultaneously, each with isolated data and independent LLM agents, all visible in one dashboard.

```
docker compose up
→ Agent0 (port 3001) joins the server
→ Agent0's Python process serves the dashboard page (:3002) and coordinator (:3010)
→ Backend service starts on :8000 and exposes the public API
→ Additional agents can register with the coordinator when enabled
```

**Chat addressing** prevents interference:
```
@Agent0 mine iron 8     → only Agent0 responds
@Agent1 fish catches 20 → only Agent1 responds
@all sethome            → both bots respond
```

Each bot's Python process writes `live_state.json` on every WebSocket tick. The backend aggregates bot state from coordinator runtime data plus per-bot files and the shared PostgreSQL database.

**Bot-to-bot isolation**: Bots ignore each other's Minecraft chat (configurable via `BOT_USERNAMES`). Coordination between bots goes through the HTTP coordinator service, not the game chat channel.

### Coordinator Task Flow

bot0 runs the coordinator service (`COORDINATOR_PORT=3010`). All other bots register with it on startup and poll for tasks:

```
bot1 startup → POST /bots/register       → coordinator creates bot1's queue
bot1 idle    → GET  /bots/bot1/tasks/next → pull model, polled every 2s tick
bot0 LLM     → POST /bots/bot1/tasks      → assign task to bot1
bot1 done    → PATCH /bots/bot1/tasks/{id}→ report completion back to coordinator
```

This is a **pull model** — bot1 asks for work rather than bot0 pushing to bot1. Benefits: bot1 needs no open port, can restart freely, and naturally avoids taking new tasks while busy. Adding bot2 requires only setting `COORDINATOR_URL=http://agent0:3010` — no coordinator changes needed.

---

## Deployment

### Docker Compose (recommended)

```bash
# Set up .env
echo "GOOGLE_API_KEY=your_key" > .env
echo "MC_HOST=your_server" >> .env

# Start the stack
docker compose up --build
```

Default services in the current MVP:
- `bot0`
- `agent0` (dashboard page on `:3002`, coordinator on `:3010`)
- `backend` (public API on `:8000`)

The `backend` service includes a Docker healthcheck that probes `GET /ready`.

### Backend API

The backend is the **only public API surface** for clients, dashboard UI, and future external tooling.

Canonical schema reference:
- [docs/backend-api.md](docs/backend-api.md)

Health and readiness:

```bash
curl http://localhost:8000/health
curl http://localhost:8000/ready
```

Bot/task APIs:

```bash
curl http://localhost:8000/bots
curl http://localhost:8000/bots/bot0/state
curl http://localhost:8000/bots/bot0/tasks
curl http://localhost:8000/tasks/<task_id>
```

Submit a task (natural language or structured):

```bash
# Natural language — LLM decomposes into commands
curl -X POST http://localhost:8000/bots/bot0/tasks \
  -H 'content-type: application/json' \
  -d '{"goal": "mine iron 8", "interrupt": false}'

# Structured — explicit command list
curl -X POST http://localhost:8000/bots/bot0/tasks \
  -H 'content-type: application/json' \
  -d '{"goal": "mine iron 8", "commands": ["mine iron 8"], "interrupt": false}'
```

Task control:

```bash
# Abort the running task
curl -X POST http://localhost:8000/bots/bot0/abort

# Resume the last interrupted task
curl -X POST http://localhost:8000/bots/bot0/resume
```

Metrics:

```bash
curl http://localhost:8000/metrics/success-rate
curl http://localhost:8000/metrics/stuck-count
curl http://localhost:8000/metrics/llm-latency
curl http://localhost:8000/metrics/goal-completion
```

### Local Development

```powershell
# JS Bot 0
cd agent/bot && node index.js

# Python Agent 0
$env:BOT_ID="bot0"; $env:BOT_DATA_DIR="agent/data/bot0"; python -m agent.agent
```

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `BOT_ID` | `bot0` | Bot identifier, used for data isolation |
| `BOT_DATA_DIR` | `agent/data/` | Per-bot data directory |
| `BOT_WS_PORT` | `3001` | JS bot WebSocket port |
| `BOT_WS_URL` | `ws://localhost:3001` | Python agent connects here |
| `BOT_USERNAMES` | `` | Comma-separated bot usernames to ignore in chat |
| `DASHBOARD_PORT` | `3002` | Dashboard page HTTP port (UI only) |
| `COORDINATOR_PORT` | `` | Set on bot0 to start the coordinator service on this port |
| `COORDINATOR_URL` | `` | Set on every agent that should register/poll against the coordinator |
| `BACKEND_CORS_ORIGINS` | `` | Extra comma-separated origins allowed to call backend APIs |
| `GOOGLE_API_KEY` | — | Gemini API key |

---

## Supported Activities

| Command | What the bot does |
|---------|-----------------|
| `mine <ore> <count>` | Mines the target ore, crafts tools if needed |
| `chop logs <count>` | Chops trees |
| `fish catches <count>` | Fishes |
| `smelt <material>` | Smelts items in a furnace (builds one if needed) |
| `hunt count <n>` | Hunts animals for food |
| `getfood count <n>` | Gets cooked food (hunts + smelts if needed) |
| `explore <target>` | Explores until finding trees/animals/ores |
| `deposit <chest_id>` | Deposits inventory to a labeled chest |
| `makechest` / `labelchest` | Creates and labels storage chests |
| `sethome` / `home` / `back` | Home base management |
| `!setmode <mode>` | `companion` / `survival` / `workflow` |

---

## Design Trade-offs

### What works well

- **Deterministic rules + LLM hybrid**: The system layer catches structurally invalid LLM outputs (duplicate commands, illegal skips, missing dependencies) before they reach the bot. This dramatically reduces the rate of unrecoverable failures.

- **LIFO stack over state machines**: Every new activity is a clean push; restoring context is a clean pop. No state machine to maintain, no explicit "resume" logic per activity type.

- **File-based working memory**: `task.json` is human-readable, debuggable, and survives process restarts. The dashboard reads it directly — no API layer needed.

- **Separate processes for JS and Python**: Each can crash and restart independently. The Python agent reconnects to the JS bot automatically on disconnect.

### Known limitations

- **LLM latency**: Each LLM call adds 1–3 seconds of latency. Deterministic shortcuts mitigate this for common cases, but complex stuck situations still feel slow.

- **Context window pressure**: Long-running sessions accumulate events and failures in `task.json`. A context compression layer (`context_builder.py` already started) needs to be extended to all skill handlers.

- **Single-point planning**: Each bot's Python agent plans independently. Without a coordinator, two bots may claim the same resource (both decide to go fish, both path to the same ore vein).

- **No persistent world model**: The bot's spatial memory (ore locations, forest areas) expires and resets. It doesn't build a map of the world across sessions.

---

## Roadmap

### Near-term
- **Metrics dashboard**: Surface the new event data (stuck count, step durations, LLM decisions) in the live dashboard
- **Context budget system**: Per-skill limits on how much history enters the LLM prompt (`context_builder` v2)
- **Tool acquisition policy**: Shared `ensureTool` retry/cooldown logic instead of per-activity reimplementation

### Mid-term
- **Pydantic AI integration**: Replace fragile `re.sub + json.loads` LLM response parsing with typed structured outputs — starting with `planner.py`
- **Interaction memory**: Persist player preferences and long-term goals across sessions
- **Reflection memory**: Bot accumulates observations about what works and what doesn't

### Multi-Agent Coordinator
- Python coordinator class with one LLM call for dynamic task assignment
- Shared resource registry: `{ "ore_vein_A": "bot0", "fishing_spot": "bot1" }`
- Bot-to-bot messaging via Python message queue (not Minecraft chat)

---

## Evaluation

All bots share a single **PostgreSQL** database (`tasks`, `events`, `failures`, `logs`) with `bot_id` as the isolation key. Task history is migrated from per-bot SQLite files.

### Event types recorded

| Event | When | Key details |
|-------|------|-------------|
| `llm_call` | After every LLM invocation | `skill`, `latency_ms`, `llm_response` (action/commands) |
| `activity_stuck` | On every stuck trigger, before handler | `activity`, `reason`, `detail`, `watchdog`, `progress` |
| `replan` | When LLM or deterministic path replans | `new_commands`, `path_taken` |
| `skip` | When a step is skipped | `path_taken` |
| `step_started` | Executor step begins | `command`, `step` |
| `step_done` | Executor step completes | `command`, `step`, `duration_ms` |
| `step_timeout` | Executor step times out (no JS heartbeat) | `command`, `step` |
| `task_started` | JS bot starts an activity | `activity`, `goal` |
| `task_stopped` | JS bot stops an activity | `activity`, `reason` |
| `activity_done` | JS bot completes an activity | `activity`, `goal`, `progress` |
| `player_died` / `player_respawned` | Bot death / respawn | `cause`, `deathPos` |
| `goal_verification_failed` | Goal check after plan | remediation commands |

### Example queries (PostgreSQL)

```sql
-- Full step-by-step trace for a task
SELECT event_type, command, step, details->>'duration_ms' AS ms, at
FROM events WHERE task_id = 'abc123' ORDER BY at;

-- Stuck events per activity in last 24h
SELECT details->>'activity', reason, COUNT(*)
FROM events WHERE event_type = 'activity_stuck' AND at > NOW() - INTERVAL '24h'
GROUP BY 1, 2 ORDER BY 3 DESC;

-- What the LLM decided on each stuck recovery
SELECT e1.details->>'activity', e1.reason,
       e2.details->>'llm_response' AS decision
FROM events e1
JOIN events e2 ON e1.task_id = e2.task_id AND e2.event_type = 'llm_call'
WHERE e1.event_type = 'activity_stuck'
ORDER BY e1.at DESC LIMIT 20;

-- Deterministic vs LLM split for stuck recovery
SELECT details->>'path_taken' AS path, COUNT(*)
FROM events WHERE event_type IN ('replan','skip')
GROUP BY path;

-- Average LLM latency per skill
SELECT details->>'skill' AS skill,
       ROUND(AVG((details->>'latency_ms')::float)) AS avg_ms,
       COUNT(*) AS calls
FROM events WHERE event_type = 'llm_call'
GROUP BY skill ORDER BY avg_ms DESC;

-- Task success/failure rate
SELECT status, COUNT(*) FROM tasks GROUP BY status;

-- Coverage gaps: activity/reason pairs still falling to LLM most often
SELECT reason, details->>'activity' AS activity, COUNT(*) AS stuck_count
FROM events WHERE event_type = 'activity_stuck'
  AND at > NOW() - INTERVAL '7d'
GROUP BY 1, 2 ORDER BY 3 DESC;
```

The coverage-gaps query is the most actionable: high counts for a given `activity/reason` pair indicate candidates for new deterministic shortcuts.

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Minecraft bot runtime | Node.js, [mineflayer](https://github.com/PrismarineJS/mineflayer) |
| Bot-agent transport | WebSocket (ws) |
| Intelligence layer | Python 3.11, asyncio |
| LLM backends | Google Gemini API, Ollama (local), Vertex AI |
| Backend API | FastAPI |
| Dashboard server | aiohttp |
| Database | PostgreSQL (asyncpg), shared across all bots |
| Deployment | Docker, Docker Compose |
| Package management | uv (Python), npm (Node) |

---

## Project Structure

```
agent/
  bot/              # JS bot — real-time game control
    bridge.js       # WebSocket server, state serialization
    activity.js     # LIFO activity stack
    commands.js     # Command dispatcher (Python → JS)
    watchdog.js     # No-progress detection
    mining.js       # Mining activity
    fishing.js      # Fishing activity
    smelting.js     # Smelting activity
    ...
  skills/           # Python skill handlers (one per event type)
    planner.py      # Natural language → command plan
    activity_stuck/ # Layered stuck recovery
    inventory.py    # Inventory management decisions
    self_task.py    # Autonomous task planning (workflow mode)
    ...
  agent.py          # Event router, WebSocket client
  executor.py       # PlanExecutor — sequences multi-step plans
  task_memory.py    # Working memory (task.json)
  dashboard.py      # Live observability server
  dashboard.html    # Single-file dark theme UI
```
