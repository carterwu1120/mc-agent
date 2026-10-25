---
name: minecraft-bot
description: Run and direct an autonomous Minecraft Java Edition bot (mineflayer). Use when the user wants an agent to join a Minecraft server and mine ores, chop wood, fish, hunt, cook, smelt, store items in chests, or carry out multi-step survival goals such as "get an iron pickaxe" or "mine 10 diamonds". The bot runs the low-level actions and watchdogs itself; you plan, sequence commands and recover when it gets stuck.
---

# Minecraft Bot

You are the planner for a mineflayer bot. The bot process does the mechanical work
(pathfinding, digging, combat, auto-eating, escaping water, pausing when the
inventory is full) and reports *events*. You turn the user's goal into a short
sequence of bot commands, run them one at a time, and decide what to do when an
event needs judgement.

All control goes through one CLI. Paths below are relative to this skill's directory:

```bash
MCCTL="node <skill-dir>/scripts/mcctl.js"
```

Every subcommand prints one JSON object. Exit code 1 means `{"error": ...}`.

## Reference files

The domain knowledge is in Traditional Chinese. The same files are used verbatim by the
bot's own planning agent, so follow them as written.

| file | read when |
|---|---|
| `references/commands.md` | before your first plan: every command and its syntax |
| `references/bot_capabilities.md` | before your first plan: what commands do and do **not** do (tool chains, food, equip, chests) |
| `references/planning.md` | turning a goal into a command sequence |
| `references/recovery.md` | `activity_stuck` / `mining_decision` |
| `references/inventory.md` | `inventory_full` |
| `references/crafting.md` | `craft_decision` |
| `references/respawn.md` | `player_respawned` |
| `references/durability.md` | `tool_low_durability` |
| `references/setup.md` | installing, connecting, troubleshooting |

## 1. Setup and connect

Requirements: Node.js 22+, and a reachable **Java Edition** server (vanilla or LAN
world). Read `references/setup.md` if anything below fails.

```bash
$MCCTL setup                       # once: installs mineflayer etc. into scripts/bot
$MCCTL start --host localhost --port 25565 --username Agent --version auto
```

- Ask the user for host, port and bot name if unknown. "Open to LAN" uses a
  random port shown in the game, not 25565.
- `--auth microsoft` is for online-mode servers; `start` prints a sign-in URL and
  code on stderr. Relay it to the user and keep waiting.
- `start` returns `{"started": true, ..., "state": {...}}` once the bot is in the
  world, otherwise the tail of the bot log. Use `--name <id>` on every command to
  run more than one bot.

## 2. The control loop

```bash
$MCCTL state                       # compact snapshot: pos, health, food, inventory, stack…
$MCCTL run mine iron 8             # send one command and block until it resolves
$MCCTL wait --timeout 300          # block until the next event that needs you
```

`run` returns `{command_id, status, events, state}`:

| status | meaning | next |
|---|---|---|
| `done` | the command finished (`action_done` / `activity_done`) | run the next step |
| `running` | an activity started and is still working when `--timeout` hit | `wait` |
| `stuck` | this command reported `activity_stuck` or `mining_decision` | recover (section 4) |
| `interrupted` | something else needs you first (see `events`) | handle that event |
| `not_started` | an activity never started (usually a full inventory) | inspect `events` and `state` |
| `sent` | fire-and-forget command (`chat`, `come`, …) | continue |

`wait` returns `{timeout, events, state}`. Every `run`/`wait` response consumes the
events it returns, so you never see an event twice; use `$MCCTL events --last 20`
to re-read history after a context reset.

**Keep the loop cheap.** Long activities (`mine diamond 10`) legitimately take
minutes. Do not poll `state` in a loop. Call `wait` with a long timeout instead, and
set your shell tool's timeout above it (for example `--timeout 300` with a 330 s
Bash timeout). A `wait` that returns `timeout: true` with the same activity and
growing `progress` means everything is fine. Wait again.

## 3. Planning

Plan with `references/planning.md` and `references/bot_capabilities.md`. Most
failed plans come from three facts in those files:

- `mine <ore> <n>` never makes its own pickaxe. Build the tool chain with
  `preparetool` first.
- `getfood` only cooks raw food already carried, `fish` needs a rod already
  carried, and `equip` never crafts.
- Every count is explicit and minimal: compute the shortfall from `state.inventory`.

Run one command at a time and wait for `done` before the next. Keep the user's
objective in your own notes (goal, target count, steps done). The bot's
`state.stack` only shows the current activity's goal and progress.

## 4. Events and how to respond

| event | what happened | your move |
|---|---|---|
| `activity_done` | goal reached, bot idle | next plan step, or report to user |
| `activity_stuck` | watchdog saw no progress, or a precondition failed (`reason`, `detail`) | `stopall`, then `references/recovery.md` |
| `mining_decision` | mining stopped itself (`reason`, `mining_result`) | same as stuck |
| `inventory_full` | bot paused at 34/36 slots and waits **120 s** for a decision | `references/inventory.md`, reply below |
| `craft_decision` | `options` to choose from (waits **20 s**, then takes the first), or `reason: material_missing` with `missing_materials` | `references/crafting.md` |
| `food_low` | hunger < 10 and no food carried | food rules in `references/bot_capabilities.md` |
| `tool_low_durability` | tool/armor ≤ 10 % | `references/durability.md` |
| `player_died` / `player_respawned` | death, then respawn at spawn point | `references/respawn.md`, then resume |
| `chat` | a player addressed the bot (`from`, `message`) | treat as an instruction from the user |
| `mc_disconnected` / `bot_exited` | server connection lost or process died | `$MCCTL log`, then `stop` and `start` again |

Decision replies use `send` with raw JSON:

```bash
$MCCTL send '{"command":"inventory_decision","action":"drop","items":[{"name":"diorite","count":64},{"name":"gravel","count":20}]}'
$MCCTL send '{"command":"inventory_decision","action":"blocked"}'
$MCCTL send '{"command":"craft_decision","item":"stone_pickaxe"}'
```

Inventory is cleaned in place only, following `references/inventory.md`: drop junk and
building blocks beyond 128 in total, then misc items the current task does not need, until
about 28 slots are used. The bot refuses to drop tools, armor, food, fuel, valuables and
crafting essentials whatever you send. There is no going home
or storing in chests. If nothing can be dropped safely, send `blocked`; the bot reports
`inventory_blocked` and you ask the player to clear space.

### Recovery budget

After `activity_stuck`/`mining_decision`, always run `stopall` before anything else.
The stuck activity is only paused, and a later activity finishing would otherwise
revive it. Then pick a strategy that changes something, as `references/recovery.md`
describes. Give up after three failed strategies for the same step. Tell the user
(and players, via `chat`) what blocks progress and what you need.

## 5. Talking to players

By default the bot only reacts to chat addressed to it (`@Agent mine iron 3` or
`@all …`). Start with `--chat all` to receive every message. Messages starting with
`!` (`@Agent !stopall`) run directly on the bot without involving you, which is the
player's emergency override. Use `$MCCTL run chat <text>` to speak in game. Keep it short.

## 6. Finishing

When the goal is met, report the result and the final inventory to the user. Leave the bot
running if the user may give more tasks. Otherwise run `$MCCTL stop`. Bot logs and data
live in `~/.mcbot/<name>/` and persists across restarts.
