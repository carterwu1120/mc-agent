# Minecraft Bot Agent — Architecture Guide

## Maintenance Rule

**When making architectural changes, update this file (and relevant sub-CLAUDE.md).**
If you add a new activity module, new event type, new skill handler, or change any core pattern — update the relevant section here before finishing the task.

**Never commit after making changes** unless the user explicitly asks you to commit.

## Project Scope

**Active codebase: `agent/` only.**

The following directories are legacy/unused — do NOT read or modify them:
- `fishing_tool/` — old CV-based fishing tool, replaced by mineflayer bot
- `training/` — old YOLO training scripts, no longer relevant

Entry points:
- `agent/bot/index.js` — start the JS bot: `node agent/bot/index.js`
- `agent/agent.py` — start the Python agent: `python -m agent.agent`

---

## Architecture

Two-process system connected by WebSocket on port 3001:

```
JS Bot (mineflayer)  ←→  WebSocket :3001  ←→  Python Agent
agent/bot/                                      agent/agent.py
```

- **JS bot** — controls the Minecraft bot directly (movement, digging, fishing, combat). Runs async activity loops. Sends events to Python when something happens.
- **Python agent** — receives events, routes to skill handlers, calls LLM, sends command strings back to JS.

For JS bot internals (activity pattern, bridge state, commands.js): see agent/bot/CLAUDE.md
For Python skill internals (skill pattern, LLM format, task_memory): see agent/skills/CLAUDE.md

---

## Key Modules

| Module | Purpose |
|--------|---------|
| `bot/bridge.js` | WebSocket server, `sendState(bot, type, extra)` — includes `mode` field |
| `bot/activity.js` | LIFO activity stack manager |
| `bot/commands.js` | Routes all Python→JS commands; checks `checkFull` before any activity |
| `bot/mode.js` | Operating mode: `getMode()`, `setMode(m)` — persisted to `agent/data/mode.json` |
| `bot/inventory.js` | Pauses top activity when full (≥34 slots), exports `checkFull(bot)` |
| `bot/chest.js` | Chest storage — persisted to `agent/data/chests.json`. Labels: `food`, `wood`, `stone`, `ore`, `misc` |
| `bot/equipment.js` | `equipBestLoadout`, `equipSpecific`, `unequipAll` |
| `bot/crafting.js` | `ensureToolFor`, `ensurePickaxeTier`, `applyCraftDecision` |
| `bot/water.js` | Background water escape monitor |
| `bot/eating.js` | Auto-eats when food < 18 |
| `bot/buried.js` | Tracks buried item positions (avoids re-digging) |
| `bot/home.js` | Base location — persisted to `agent/data/home.json`. `back` tps to top activity's `startPos` |
| `bot/world.js` | Utility: `findNearestPlayer`, entity/block helpers |
| `agent.py` | Event router — maps event types to skill handlers |
| `executor.py` | Sequences plan commands, waits for `action_done`/`activity_done` between steps |
| `task_memory.py` | Persists current task to `agent/data/task.json` |
| `brain/` | `GeminiClient` / `OllamaClient` — both implement `LLMClient` |

---

## Event Types (JS → Python)

| Type | Meaning | Python handles? |
|------|---------|----------------|
| `tick` | Heartbeat every 2s | Yes — self_task (idle only, 60s cooldown) |
| `activity_done` | Goal reached, bot idles | Yes — signals PlanExecutor + marks task done |
| `action_done` | Instant action completed | Yes — signals PlanExecutor |
| `task_started` | Activity started (via `!` command or LLM) | Yes — saves to task_memory for resume |
| `task_stopped` | Activity manually stopped | Yes — marks task_memory interrupted |
| `activity_stuck` | Stuck mid-activity | Yes — LLM decides recovery |
| `inventory_full` | Inventory ≥ 34 slots | Yes — LLM decides drop/plan |
| `craft_decision` | Needs crafting decision | Yes |
| `food_low` | Food < 10 & idle & no food | Yes — deterministic, no LLM |
| `chat` | Player sent a message | Yes — planner skill |
| `player_died` | Bot health → 0 | Yes — aborts executor, writes `agent/data/death.json` |
| `player_respawned` | Bot respawned | Yes — LLM respawn skill |
| `tool_low_durability` | Main hand ≤ 10% durability (every 5s, 60s cooldown) | Yes — LLM decides |

**Key rule:** `activity_done` is NOT routed to LLM — it signals PlanExecutor only. Only `activity_stuck` triggers LLM intervention.

---

## Skill Files (`agent/skills/`)

| File | Triggered by | Purpose |
|------|-------------|---------|
| `planner.py` | `chat` event | 自然語言 → command 序列；偵測「繼續」→ 從 task_memory 恢復 |
| `inventory.py` | `inventory_full` | LLM decides drop / deposit plan / continue |
| `activity_stuck.py` | `activity_stuck` | 單步恢復；executor 執行中可回傳 `replan` 替換剩餘步驟 |
| `self_task.py` | `tick` (idle, 60s) | 自主任務規劃；companion mode 不執行 |
| `respawn.py` | `player_respawned` | LLM 決定重生後恢復計畫 |
| `tool_durability.py` | `tool_low_durability` | LLM 決定工具耐久不足的處理 |
| `food.py` | `food_low` | 補充食物（確定性，不呼叫 LLM） |
| `craft_decision.py` | `craft_decision` | Decide what to craft |
| `task_arbitration.py` | called by `_handle_player_chat` | 判斷玩家訊息是否 interrupt/queue/defer 當前任務 |
| `commands_ref.py` | imported by skills | Canonical command registry — `command_list(keys)` helper. **Never duplicate command descriptions inline.** |
| `state_summary.py` | imported by skills | `summary_json(state)`, `equipment_summary(state)` |

---

## Architecture Principles

1. **Activities are tools** — each module is a dumb executor. It runs, finishes or gets stuck, then reports. It never decides what comes next.
2. **goal_reached → idle** — bot stops and waits. Next command comes from player or orchestration.
3. **stuck → LLM** — only `activity_stuck` triggers LLM intervention.
4. **LIFO stack** — pushing auto-pauses current top; popping auto-resumes previous.
5. **Prompts live in skills** — system prompts belong in `agent/skills/`, not in JS.
6. **Command descriptions live in `commands_ref.py`** — import `command_list(keys)`, never duplicate inline.
7. **Death → LLM** — `player_died` aborts executor; `player_respawned` routes to `respawn.py`.
8. **Inventory check at dispatch** — `commands.js` calls `checkFull(bot)` before any activity command. Do not add per-activity checks.
9. **No-progress beats not-done** — long-running activities (for example `mine diamond 10`) may legitimately take minutes. Stuck detection must be based on lack of meaningful progress, not merely lack of `activity_done`.
10. **Recovery is layered** — JS bot handles mechanical/local recovery first (movement, placement, watchdog), Python handles orchestration/plan recovery, and LLM handles ambiguous strategy decisions.

---

## Stuck / Recovery Model

The system uses a layered stuck model:

1. **Semantic progress** — activity-specific progress that directly advances the activity goal.
2. **Physical progress fallback** — generic signals such as movement, inventory changes, held-item changes, goal/progress mutations.
3. **No-progress watchdog** — if the current top activity remains active but stale past timeout, JS emits `activity_stuck` with `reason='no_progress'`.

Important rules:
- Do not treat "no `activity_done` for N seconds" as stuck by itself.
- `activity_stuck(reason='no_progress')` is valid even while an activity is still running.
- New JS activities should automatically be covered by the watchdog via global defaults; only special cases should override timeout or suggested actions.
