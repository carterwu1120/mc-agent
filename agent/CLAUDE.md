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

CLI planning providers: `LLM_PROVIDER=codex|claude` selects `brain/cli_agent.py`
through `brain/factory.py` (default: Codex). Direct model API providers and SDK
dependencies have been removed on this branch; invalid/old providers fail with
migration instructions instead of falling back to another provider.
Each skill call starts a bounded noninteractive CLI process, forwards its prompt
via stdin, and returns the final decision to the existing skill parser. By
default, `CLI_SESSION_REUSE=true` resumes one provider conversation per bot;
`BOT_DATA_DIR/cli_session.json` stores the provider/model/session ID across
container recreation. Codex uses its emitted thread ID with `exec resume`, while
Claude uses `--session-id` and `--resume`. A provider/model mismatch starts a new
session, and the old isolated behavior remains available with
`CLI_SESSION_REUSE=false`. Python retains task memory, event routing, and
PlanExecutor ownership; CLI sessions do not directly control Minecraft. CLI
calls are serialized, with timeout and cancellation cleanup. Install and
authenticate the CLI in the agent's runtime.
Full state summaries still go to the selected CLI, but routine logs are compact:
tick lines and prompt bodies are opt-in through `AGENT_LOG_TICKS` and
`AGENT_LOG_PROMPTS`. Completion events must not be labeled as LLM calls.
Material-missing and respawn handlers use capability/environment/task history to
produce validated short prerequisite plans rather than fixed resource mappings
or unconditional `tp -> equip -> resumetask` sequences. The runtime appends the
task resume only after those prerequisites pass command validation.
CLI subprocesses omit inherited API keys and third-party provider overrides;
Codex forces ChatGPT login, Claude selects claude.ai login. Claude auth status
must report a logged-in claude.ai account before inference; saved API/Console
authentication is rejected. Codex user config is ignored; use LLM_MODEL for an
explicit model override. Subscription usage limits
and any account-level extra usage settings still apply.
The Docker agent image includes both CLIs. Its HOME is persisted in the cli_home
volume; authenticate inside that runtime before starting the agent.
The image installs production dependencies at build time and starts with
`/app/.venv/bin/python` directly. Do not use a syncing `uv run` in the runtime
CMD; it can reinstall the dev group into the container writable layer on every
recreation and create avoidable disk I/O.

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

Mining navigation pilot: `bot/navigation.js` is opt-in via
`MC_ADAPTIVE_NAVIGATION=true`, plans short walk/dig/build segments and records
bounded failure memory. Mining cancellation uses loop generations; adaptive stack
resume replans from the current location. Latest results are attached as `navigation`
in bridge state and stuck prompts. See `bot/CLAUDE.md` and
`docs/local-testing.md` for offline-vs-live validation boundaries and setup.

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
| `brain/` | `build_client()` selects Codex or Claude Code; `CLIAgentClient` implements `LLMClient` |

---

## Event Types (JS → Python)

| Type | Meaning | Python handles? |
|------|---------|----------------|
| `tick` | Heartbeat every 2s | Yes — self_task (idle only, 60s cooldown) |
| `activity_done` | Goal reached, bot idles | Yes — signals PlanExecutor + marks task done |
| `action_done` | Instant action completed | Yes — signals PlanExecutor |
| `task_started` | Activity started (via `!` command or LLM) | Yes — saves to task_memory for resume |
| `task_stopped` | Activity manually stopped | Yes — marks task_memory interrupted |
| `activity_stuck` | Stuck, expired wait, orphaned pause or unexpected idle | Yes — RecoverySupervisor |
| `recovery_paused` | Correlated JS suspension acknowledgement | Yes — resolves stop deadline; never action_done |
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
| `recovery.py` | supervised `activity_stuck` | Validated replacement strategy or explicit wait for player |
| `self_task.py` | `tick` (idle, 60s) | 自主任務規劃；companion mode 不執行 |
| `respawn.py` | `player_respawned` | LLM 決定重生後恢復計畫 |
| `tool_durability.py` | `tool_low_durability` | LLM 決定工具耐久不足的處理 |
| `food.py` | `food_low` | 補充食物（確定性，不呼叫 LLM） |
| `craft_decision.py` | `craft_decision` | Decide what to craft |
| `task_arbitration.py` | called by `_handle_player_chat` | 判斷玩家訊息是否 interrupt/queue/defer 當前任務 |
| `commands_ref.py` | imported by skills | Canonical command registry — `command_list(keys)` helper. **Never duplicate command descriptions inline.** |
| `state_summary.py` | imported by skills | `summary_json(state)`, `equipment_summary(state)` |
| `stuck/tool_acquisition.py` | imported by stuck handlers | Shared tool-acquisition retry fingerprinting and replan helpers |

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

`recovery.py` owns a single recovery per bot. `agent.py` routes all activity_stuck
(including executor verification/timeouts) through it and `skills/recovery.py`.
The previous `skills/activity_stuck.py` rules remain for replay/reference, but are
not the live event handler. Local mechanical navigation remains separate.

1. JS watches new spatial cells, inventory high-water marks and output counters.
   Repeated touches, gear changes, equal counters and short loops cannot keep it alive.
2. Default no-effect threshold is 25s (fishing 60s). Orphaned pauses trigger at 45s;
   named waits have nonrenewable deadlines. Resource work without relevant output
   requests review after 180s even if the bot keeps traveling.
3. Python detects unfinished tasks which remain idle for 15s, including companion
   mode. Instant commands have a 45s completion deadline; heartbeats alone do not
   extend these deadlines. Intentional idle/manual stops do not trigger recovery.
4. Supervisor sends `suspend` with request_id, waits at most 5s for recovery_paused,
   then calls the CLI. JS revokes command/activity scopes, pauses every frame without
   resuming old routes, clears motion and reports local cancellation success.
5. Decision deadline is RECOVERY_DECISION_TIMEOUT (60s default), two attempts per
   episode, at most three episodes in a rolling 5 minutes between explicit tasks.
   Duplicate events coalesce. A repeated strategy in the same position/inventory/
   goal fingerprint is rejected. These are call budgets, not exact token quotas.
6. A valid plan includes commands and an observable expected_outcome. The latter
   is explanatory context, not a generic executable predicate; existing step/goal
   verification and watchdog observations still determine actual progress.
7. The current executor/coordinator retains ownership during replan. No response,
   invalid/empty plans, missing stop acknowledgement or exhausted budget preserve
   the task as recovery_blocked and enter waiting_player. Autonomous ticks cannot
   overwrite it. A player instruction can restart/replan explicitly.
8. New tasks, manual stops, death/disconnect invalidate pending AI responses.
   Commands carry correlation IDs; late completions cannot complete a different
   command. PlanExecutor clears completion flags before sending, and old runs may
   not clear the replacement run's command in finally blocks.

`live_state.json` includes supervision phase/deadline/expected_outcome. Recovery
attempts and final blocking reasons are recorded in task history. Runtime waits and
command IDs are included in bridge snapshots. See bot/CLAUDE.md for scope rules.
Offline tests cover the protocol and adversarial async timelines; they cannot prove
server physics, permissions, block placement or every plugin API is cancellable.
