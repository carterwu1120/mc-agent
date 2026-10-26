# Minecraft LLM Agent

A Minecraft bot controlled through Mineflayer, with task planning powered by Codex or Claude Code CLI.
Supports multi-step execution, inventory management, and bounded recovery attempts.
Model decisions and in-game behavior still require live validation.

The local test environment uses **Minecraft Java Edition 1.20.1 + Java 17**.
Start a dedicated test server as described below, or open a single-player world to LAN.
See the [local testing guide](docs/local-testing.md) for connection troubleshooting and navigation/recovery checks (in Chinese).

### Mining checkpoints

For explicit goals such as `mine diamond 10`, task context retains the original
inventory baseline and requested quantity across recovery plans. Completion requires
the requested net inventory increase; finishing a prerequisite plan is not success.
This first version recognizes exact `mine <resource> <count>` task goals. Depositing
or consuming the target item reduces measured progress; it is not a lifetime pickup ledger.

Named mining tasks pursue the requested ore only. Missing tools yield a decision;
the planner can call `preparetool iron_pickaxe` after gathering/smelting materials.
`equip` only equips owned gear. Mining reports `mining_progress` on phase changes
and `mining_decision` with inventory/position deltas when tools or routes block work.
There is no periodic stop-and-replan timer; `MINING_SEGMENT_SECONDS` is no longer used.
The watchdog distinguishes ore output from phase progress: new best depth toward
the target counts during descent, and newly visited cells count during collection
and exploration. Returning to an already reached depth does not renew strategy progress.
Operation timeouts and existing inactivity/review windows remain safeguards when
progress stops, not total task deadlines. New cells are a local exploration proxy,
not proof that the mining strategy will eventually find diamonds; asynchronous LLM
strategy review is not implemented. Rebuild bot and agent images to activate changes.

## Local Development

### Dedicated local test server (Vanilla 1.20.1)

Use this setup for a separate test world with a fixed port instead of opening a world to LAN.
Use **Minecraft Java Edition 1.20.1** for the game client, server, and bot.
The server requires **Java 17**; check your installation with `java -version`.

1. Open the [official Minecraft 1.20.1 release page](https://www.minecraft.net/en-us/article/minecraft--java-edition-1-20-1)
   and select **Minecraft server jar** under **Cross-platform server jar**.
2. Create `local-server/` in the repository root and save the download as `local-server/server.jar`.
3. From the repository root, open PowerShell and run:

```powershell
cd local-server
java -Xms1G -Xmx2G -jar server.jar nogui
```

The first launch extracts dependencies, creates configuration files, and stops because the EULA has not been accepted.
Read the terms linked in `eula.txt`. If you agree, change `eula=false` to `eula=true`, then run the same Java command again.
Wait for `Done` before connecting. Keep the terminal open; enter `stop` to save the world and shut down cleanly.

In the **1.20.1** game client, select **Multiplayer → Direct Connection** and enter `localhost:25565`.
Stop any other server already using port `25565` on the same computer.
Set the connection options in the repository-root `.env`:

```dotenv
MC_HOST=127.0.0.1
MC_PORT=25565
MC_VERSION=1.20.1
MC_FORGE=false
```

Match the bot's authentication settings to `local-server/server.properties`:

- Default `online-mode=true`: use `MC_AUTH=microsoft` and a separate bot account that owns Java Edition.
  Set `MC_USERNAME` to that account's login identifier, usually its email address.
- Same-computer offline testing only: stop the server, set `online-mode=false` and `server-ip=127.0.0.1`, then restart.
  Use `MC_AUTH=offline` and `MC_USERNAME=Agent`. This accepts only local connections, not friends or virtual machines.

Follow the quick start below to install dependencies and launch the JS bot and Python agent.
Skip the LAN setup and keep the dedicated server's port and authentication settings.

**Git stores the instructions for recreating this environment.** `.gitignore` excludes all of `/local-server/`,
including server binaries, dependencies, EULA acceptance, local settings, logs, player data, and worlds.
Download the specified version and accept the EULA on each computer; do not commit server binaries or saves.

<a id="windows-local-quick-start"></a>

### Windows quick start (single-player LAN world)

Run these commands in PowerShell from the repository root.
Requires **Node.js 22+**, **Python 3.10–3.12**, **uv**, and either Codex or Claude Code CLI installed and signed in with a subscription account.

1. Install dependencies and create the configuration file, preserving an existing `.env`:

```powershell
uv sync --group dev
npm.cmd ci --prefix agent/bot
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

2. Open a **Java Edition** single-player world, press `Esc`, select **Open to LAN**, and note the displayed port.
   Edit **`.env`**, not `.env.example`. For a game and bot on the same computer:

```dotenv
MC_HOST=127.0.0.1
MC_PORT=51234
MC_USERNAME=Agent
MC_VERSION=1.20.1
MC_AUTH=offline
MC_FORGE=false
MC_ADAPTIVE_NAVIGATION=true
BOT_WS_URL=ws://localhost:3001
LLM_PROVIDER=codex
LLM_MODEL=
CLI_AGENT_TIMEOUT=120
CLI_REASONING_EFFORT_DEFAULT=low
CLI_SESSION_REUSE=false
AGENT_LOG_PROMPTS=false
AGENT_LOG_TICKS=false
RECOVERY_DECISION_TIMEOUT=60
```

Replace `51234` with the current LAN port and match `MC_VERSION` to your world.
`MC_AUTH=offline` only works where offline login is accepted. For authenticated servers,
use `microsoft` with a separate bot account as described in the [local testing guide](docs/local-testing.md).
Set `LLM_PROVIDER=claude` to use Claude Code.

`MC_ADAPTIVE_NAVIGATION=true` enables experimental mining navigation.
Stuck/idle recovery supervision is enabled by default and does not depend on this option.
Restart the relevant process after changing settings.

3. In one terminal, check the configuration and start the JS bot:

```powershell
npm.cmd run check --prefix agent/bot
node agent/bot/index.js
```

4. In another terminal, start the Python agent (see [planning providers](#codex--claude-code-planning-providers) for CLI login):

```powershell
uv run -m agent.agent
```

5. In game chat, switch to companion mode to prevent autonomous idle tasks, then send a test request:

```text
@Agent !setmode companion
@Agent Please mine 3 iron ore
```

Replace `@Agent` with the bot's actual in-game name.
Use `@Agent !mine stone 3` to test an action directly, and `@Agent !stopmine` to stop mining.
Press `Ctrl+C` in each bot/agent terminal when finished.

Minimal local testing does not require `DATABASE_URL`, `COORDINATOR_PORT`, or `COORDINATOR_URL`.
Full dashboard data and history queries require the backend and PostgreSQL; use [Docker Compose](#docker-compose) for the complete stack.
For Docker connecting to a game on the host, use `MC_HOST=host.docker.internal`,
`MC_USERNAME_0=Agent0`, and address the bot as `@Agent0` in chat.

See the [local testing guide](docs/local-testing.md) for offline tests, troubleshooting, and in-game acceptance checks.

## Codex / Claude Code planning providers

For local development, install and authenticate either CLI, then set:

```powershell
$env:LLM_PROVIDER="codex" # or "claude"
# Remove any previous provider-specific model override:
Remove-Item Env:LLM_MODEL -ErrorAction SilentlyContinue
python -m agent.agent
```

The CLI uses its subscription login and built-in default model. `LLM_MODEL` can
override the model. Optional `CODEX_CLI_PATH` / `CLAUDE_CLI_PATH` selects an
executable (not a shell command); `CLI_AGENT_TIMEOUT` defaults to 120 seconds.
Every handler uses `CLI_REASONING_EFFORT_DEFAULT` (default `low`); only recovery
raises a third attempt or a rejected decision to `medium`.
Each call is a fresh CLI session by default, because every prompt already carries
the full state; resuming would re-send earlier turns and grow input on every call.
`CLI_SESSION_REUSE=true` instead resumes one provider conversation within a task. The
session identifier and counters are stored in `BOT_DATA_DIR/cli_session.json`,
so they survive an agent container recreation. A completed task starts a fresh
session; the adapter also rotates at `CLI_SESSION_MAX_TURNS` (default 8) or
`CLI_SESSION_MAX_INPUT_TOKENS` (default 200000). Provider or model changes start
a separate session.
Normal logs omit two-second tick lines and full prompt bodies. Set
`AGENT_LOG_TICKS=true` or `AGENT_LOG_PROMPTS=true` only for focused debugging;
these settings change terminal verbosity, not the state sent to the model.
No model API key is required. Codex is restricted to ChatGPT login and Claude
selects claude.ai login. Inherited model API keys, custom API URLs and cloud
provider overrides are excluded from the CLI environment; there is no API
fallback. Codex user configuration is ignored, so use LLM_MODEL to select a model.
Before each Claude decision, the adapter checks `claude auth status` and refuses
API/Console authentication before sending the game prompt.
Subscription usage limits and any account-level extra usage settings still apply.
Structured `usage_limit_exceeded` failures pause model calls until the reported
reset time. If no reset time is supplied, `CLI_USAGE_LIMIT_COOLDOWN` (default
3600 seconds) is used; the cooldown is persisted across container restarts.

If an existing .env sets LLM_PROVIDER=gemini/openai/vertex/ollama, change it to
codex or claude and clear the old LLM_MODEL. Old providers now fail explicitly;
their Python SDKs and implementations have been removed.

This is a planning adapter: existing handlers send game state to `codex exec` or
`claude --print`, then validate and execute the returned JSON through
PlanExecutor and Mineflayer. Each decision still uses a bounded CLI subprocess,
and starts a fresh provider session by default; Python task memory remains the
durable source of game-task state. Set `CLI_SESSION_REUSE=true` to resume a saved
session instead. Claude built-in tools and MCP are disabled.
Codex starts in a temporary directory with a read-only sandbox and ignores user
CLI config. Session reuse does not expose Minecraft tools to the CLI agent.

For the simplest setup, sign in to Codex using `codex login` (ChatGPT account)
or run `claude` and choose your Claude subscription, then run the Python agent
locally. On Windows, use `codex.cmd login` if PowerShell blocks the npm .ps1 shim.
The adapter resolves npm launchers directly without changing execution policy.

Subscription authentication: [Codex authentication](https://developers.openai.com/codex/auth)
and [Claude subscription setup and usage limits](https://support.claude.com/en/articles/11145838-use-claude-code-with-your-pro-or-max-plan).

References: [Codex noninteractive mode](https://developers.openai.com/codex/noninteractive)
and [Claude Code programmatic usage](https://code.claude.com/docs/en/headless).

## Tests

Run from the repository root after installing dependencies as described above:

```powershell
uv run --group dev pytest test -q
npm.cmd test --prefix agent/bot
```

To test only the CLI provider adapter:

```powershell
uv run --group dev pytest test/test_cli_agent.py -q
```

CLI provider tests use fake CLIs and require no login or model requests. JS tests do not start Minecraft.
Automated tests do not replace live authentication, server connection, and in-game validation.

## Docker Compose

Use Docker Desktop with Linux containers. Node.js, Python, and both planning CLIs
are installed inside the images; you do not need host npm or uv for this workflow.
The Minecraft server still runs separately on Windows with Java 17.

For the local Vanilla server, set these values in the repository-root `.env`:

```dotenv
MC_HOST=host.docker.internal
MC_PORT=25565
MC_VERSION=1.20.1
MC_FORGE=false
MC_USERNAME_0=Agent0
LLM_PROVIDER=codex
```

Compose uses `MC_USERNAME_0`, not the native-launch `MC_USERNAME` setting.
Keep `server-ip=` blank in `local-server/server.properties` so Docker can connect;
the loopback-only setting from the native offline example will not work here.
Match authentication before starting:

- For a private offline test server, set `online-mode=false` and `MC_AUTH=offline`.
  This disables account verification; keep the server off the public internet.
- To keep `online-mode=true`, set `MC_AUTH=microsoft` and `MC_USERNAME_0` to the
  separate bot account's login identifier. Follow the bot logs to complete login;
  address the bot by its actual in-game name afterward.

Restart Minecraft after changing `server.properties`, and wait for `Done`.
Run the following commands from the repository root:

```bash
# Copy .env.example to .env; set MC_HOST and LLM_PROVIDER=codex or claude.
# Build the agent image (includes both CLIs).
docker compose build agent0

# Authenticate the chosen CLI inside the container, using your subscription.
# Codex: complete the device login URL shown in the terminal.
docker compose run --rm --no-deps agent0 codex login --device-auth
# Or Claude: select the subscription login and follow its browser instructions.
# docker compose run --rm --no-deps agent0 claude

# Start the stack.
docker compose up --build
```

Use `docker compose config --quiet` to validate configuration and
`docker compose logs -f bot0 agent0` to inspect connection/planning errors.
For the offline example, try `@Agent0 !setmode companion`, then `@Agent0 !mine stone 3`
before sending a natural-language task. Stop containers with `docker compose down`
and stop Minecraft separately with `stop`. Avoid `docker compose down -v` unless
you intend to remove the database and CLI login volumes.

.dockerignore excludes the local Minecraft server, host dependencies, credentials,
and runtime data from image builds. The data/log bind mounts still persist runtime
output on the host.

Container CLI login is stored in the named `cli_home` volume and persists
across restarts. Host CLI login is separate. Do not delete this volume unless
you intend to sign in again. Authentication is interactive setup; the running
Minecraft agent uses noninteractive CLI calls.

Default services in the current MVP:
- `postgres` (shared history database)
- `bot0`
- `agent0` (dashboard page on `:3002`, coordinator on `:3010`)
- `backend` (public API on `:8000`)

The `backend` service includes a Docker healthcheck that probes `GET /ready`.

## Environment Variables

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
| `LLM_PROVIDER` | `codex` | `codex` or `claude`; restart after changing |
| `LLM_MODEL` | CLI default | Optional model supported by your subscription |
| `CLI_AGENT_TIMEOUT` | `120` | Maximum seconds per CLI call |
| `CLI_REASONING_EFFORT_DEFAULT` | `low` | Default reasoning effort (Codex `model_reasoning_effort`, Claude `--effort`); complex handlers override per request |
| `CLI_SESSION_REUSE` | `false` | Resume one Codex/Claude conversation per bot and persist its ID in `BOT_DATA_DIR` |
| `CLI_SESSION_MAX_TURNS` | `8` | Rotate a reused CLI session after this many successful model turns |
| `CLI_SESSION_MAX_INPUT_TOKENS` | `200000` | Rotate when cumulative session input reaches this threshold |
| `CLI_USAGE_LIMIT_COOLDOWN` | `3600` | Fallback pause in seconds when a usage-limit error has no reset time |
| `AGENT_LOG_PROMPTS` | `false` | Print complete model prompts instead of compact character counts |
| `AGENT_LOG_TICKS` | `false` | Print every two-second state tick |
| `RECOVERY_DECISION_TIMEOUT` | `60` | Seconds per recovery decision; the earlier CLI/recovery deadline wins |
| `MC_ADAPTIVE_NAVIGATION` | `false` | Opt-in experimental mining navigation; recovery supervision is always enabled |
| `CODEX_CLI_PATH` / `CLAUDE_CLI_PATH` | PATH lookup | Optional executable path |

## Supported Activities

| Command | What the bot does |
|---------|-----------------|
| `mine <ore> <count>` | Mines the target ore; reports missing tools for planning |
| `preparetool <pickaxe>` | Prepares the requested minimum pickaxe tier from available materials |
| `chop logs <count>` | Chops trees |
| `fish catches <count>` | Fishes |
| `smelt <material> <count>` | Smelts or cooks carried input in a furnace (builds one if needed) |
| `hunt count <n>` | Hunts animals for food |
| `getfood count <n>` | Cooks raw food already carried; never hunts |
| `back` | Returns to the latest task's start position |
| `!home` / `!sethome` / `!deposit <id>` | Manual-only base and chest commands; planners never use them |
| `!setmode <mode>` | `companion` / `survival` / `workflow` |

## Architecture

```text
Minecraft server ↔ Mineflayer bot ↔ Python agent ↔ Codex / Claude Code CLI
                              WebSocket :3001

Dashboard :3002 → Backend API :8000 → Coordinator :3010 / state files / PostgreSQL
```

- `agent/bot/`: game connection, movement, gathering, activity stack, and stall detection.
- `agent/brain/`, `agent/handlers/`: CLI adapters and event handlers.
- `agent/executor.py`, `agent/recovery.py`: plan execution, result verification, and bounded recovery retries; waits for player help when needed.
- `agent/task_memory.py`: task goals, execution progress, and recent events.
- `backend/`: public API; the dashboard serves the page and fetches data from the backend.

Food is handled conservatively; see "Food handling is weak" under Known limitations.

Each bot uses a separate `BOT_ID` and data directory, and registers with the coordinator to pull tasks.
Address bots with `@Agent0` or `@Agent1`; `BOT_USERNAMES` identifies other bots whose chat should be ignored.

## Backend API

See the [Backend API Contract](docs/backend-api.md) for endpoints and request/response schemas.
With the full Docker stack running, check:

```powershell
curl.exe http://localhost:8000/health
curl.exe http://localhost:8000/ready
curl.exe http://localhost:8000/bots
```

Dashboard: <http://localhost:3002>. Public API: <http://localhost:8000>.
Coordinator port `3010` is internal and is not a client-facing API.

## Known limitations

- Each model decision starts a CLI subprocess, adding latency and consuming subscription usage.
- Forge handshaking does not imply full mod compatibility; custom blocks, items, and packets need separate validation.
- `MC_ADAPTIVE_NAVIGATION` is experimental mining navigation; synthetic terrain tests do not guarantee success in every real world.
- Multiple bots may compete for the same resources; there is no complete world map persisted across sessions.

### Food handling is weak

The bot cannot keep itself fed. It only eats what it carries and asks the player for the rest.

- **No autonomous food gathering.** When hunger drops below 10 and the inventory has no food,
  the bot sends `food_low`, pauses the current task and asks the player for food
  (`handlers/food.py`, no model call). It does not hunt, fish or farm on its own. The
  `waiting_for_change` retry only asks the player again; `_build_food_plan` (hunt/fish/getfood
  quantities) exists and is tested but is not wired into `food_low`.
- **Resume depends on the player.** The task resumes once hunger is back to 10 or more,
  which in practice means the player gave food and the bot ate it.
- **Food sources are local only.** `hunt` needs animals in view, `fish` needs a fishing rod
  (never crafted) next to water, and `getfood` only cooks raw food already carried. With
  exploration removed, nothing searches farther away.
- **Eating picks the first edible item, not the best one.** It may eat raw chicken (poison
  risk) or rotten flesh (hunger effect) before cooked food further down the inventory.
- **Eating is deferred during mining, chopping, hunting and combat** until hunger is below
  10, so long tasks often run on a low bar and stop when food runs out mid-task.
- **Planning food in advance is up to the planner.** Food for a long task is only gathered if
  the plan includes it (guidance in `agent/knowledge/bot_capabilities.md`); nothing tops it up
  later.

Practical advice: give the bot a stack of cooked food before long tasks (deep mining).

## Exploration removed

自主探索與持久化資源位置記憶已移除，不再讀寫 `exploration_memory.json`。
保留當下附近方塊／生物觀察、一般移動、上地表及挖礦內部下挖／挖隧道。
附近缺少來源時請玩家補給或帶路，不以隨機傳送代替探索。
舊任務的探索指令會回報不支援，需重新規劃。無本地食物來源時進入
`waiting_for_change`，食物條件改善後才重新評估。
