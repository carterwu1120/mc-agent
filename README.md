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

### Two-Process Design

```
┌─────────────────────────────────────────────────┐
│  JS Bot (Node.js + mineflayer)                  │
│  Real-time game control: movement, digging,     │
│  fishing, crafting, inventory management        │
│                                                 │
│  Activity Stack (LIFO)                          │
│  Watchdog (no-progress detection)               │
└─────────────────┬───────────────────────────────┘
                  │ WebSocket :3001
                  │ (JSON events + commands)
┌─────────────────▼───────────────────────────────┐
│  Python Agent                                   │
│  Intelligence layer: LLM planning, skill        │
│  routing, task memory, executor                 │
│                                                 │
│  Dashboard HTTP server (:3002)                  │
└─────────────────────────────────────────────────┘
```

**Why two processes?**
- JS (mineflayer) has the best ecosystem for real-time Minecraft control
- Python has the best ecosystem for LLM integration, async orchestration, and data persistence
- Clear boundary: JS reports what happened → Python decides what to do next

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
- **Post-action verification**: compares before/after state after equip/smelt/mine/deposit — if the action didn't take effect, routes back to the LLM for recovery
- **Replan during execution**: the LLM can replace remaining steps mid-plan via `{"action": "replan", ...}`
- **Step skip / abort**: granular control without losing the overall task context

### 4. Working Memory (`task_memory`)

`task.json` functions as the bot's short-term working memory:

```json
{
  "goal": "mine diamond 10",
  "final_goal": "mine diamond 10",
  "steps": [...],
  "currentStep": 2,
  "status": "running",
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

A live HTTP dashboard (aiohttp, port 3002) shows all agent state in real time:

- Health / food bars, position, activity
- Current task with step-by-step progress bar
- Equipment durability, inventory, chest contents
- Recent events and failure log
- Internal state (thinking, queued tasks, stuck events)

The `/state` endpoint returns a **multi-agent ready schema**:
```json
{ "coordinator": null, "agents": [{ "id": "bot0", ... }, { "id": "bot1", ... }] }
```
The frontend iterates over `agents[]` — adding a new bot requires no frontend changes.

---

## Multi-Agent Support

Multiple bots can run simultaneously, each with isolated data and independent LLM agents, all visible in one dashboard.

```
docker compose up
→ Agent0 (port 3001) + Agent1 (port 3003) join the server
→ Agent0's Python process serves the dashboard
→ Agent1 writes live_state.json every tick; Agent0's dashboard reads it
```

**Chat addressing** prevents interference:
```
@Agent0 mine iron 8    → only Agent0 responds
@Agent1 fish catches 20 → only Agent1 responds
@all sethome           → both bots respond
```

Each bot's Python process writes `live_state.json` on every WebSocket tick. The dashboard aggregates own-bot state (from memory) with remote bots (from files) — no shared database required.

**Bot-to-bot isolation**: Bots ignore each other's Minecraft chat (configurable via `BOT_USERNAMES`). Coordination between bots will go through a Python coordinator layer, not the game chat channel.

---

## Deployment

### Docker Compose (recommended)

```bash
# Set up .env
echo "GOOGLE_API_KEY=your_key" > .env
echo "MC_HOST=your_server" >> .env

# Start both bots
docker compose up --build
```

Services: `bot0`, `agent0` (with dashboard on :3002), `bot1`, `agent1`.

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
| `DASHBOARD_PORT` | `3002` | Dashboard HTTP port |
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
- **Resource-aware planning**: Coordinator tracks which bot has claimed which activity, preventing resource conflicts
- **Context budget system**: Per-skill limits on how much history enters the LLM prompt
- **Tool acquisition policy**: Shared `ensureTool` retry logic instead of per-activity reimplementation

### Mid-term
- **Pydantic AI integration**: Replace fragile `re.sub + json.loads` LLM response parsing with typed structured outputs — starting with `planner.py`
- **Interaction memory**: Persist player preferences and long-term goals across sessions
- **Reflection memory**: Bot accumulates observations about what works and what doesn't

### Multi-Agent Coordinator
- Python coordinator class with one LLM call for dynamic task assignment
- Shared resource registry: `{ "ore_vein_A": "bot0", "fishing_spot": "bot1" }`
- Bot-to-bot messaging via Python message queue (not Minecraft chat)

---

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Minecraft bot runtime | Node.js, [mineflayer](https://github.com/PrismarineJS/mineflayer) |
| Bot-agent transport | WebSocket (ws) |
| Intelligence layer | Python 3.11, asyncio |
| LLM backends | Google Gemini API, Ollama (local) |
| Dashboard server | aiohttp |
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
