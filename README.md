# Minecraft LLM Agent

A Minecraft bot controlled through Mineflayer, with task planning powered by Codex or Claude Code CLI.
Supports multi-step execution, inventory management, and bounded recovery attempts.
Model decisions and in-game behavior still require live validation.

The local test environment uses **Minecraft Java Edition 1.20.1 + Java 17**.
Start a dedicated test server as described below, or open a single-player world to LAN.
See the [local testing guide](docs/local-testing.md) for connection troubleshooting and navigation/recovery checks (in Chinese).

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
No model API key is required. Codex is restricted to ChatGPT login and Claude
selects claude.ai login. Inherited model API keys, custom API URLs and cloud
provider overrides are excluded from the CLI environment; there is no API
fallback. Codex user configuration is ignored, so use LLM_MODEL to select a model.
Before each Claude decision, the adapter checks `claude auth status` and refuses
API/Console authentication before sending the game prompt.
Subscription usage limits and any account-level extra usage settings still apply.

If an existing .env sets LLM_PROVIDER=gemini/openai/vertex/ollama, change it to
codex or claude and clear the old LLM_MODEL. Old providers now fail explicitly;
their Python SDKs and implementations have been removed.

This is a planning adapter: existing skills send game state to `codex exec` or
`claude --print`, then validate and execute the returned JSON through
PlanExecutor and Mineflayer. Each request starts a fresh session; Python task
memory supplies continuity. Claude built-in tools and MCP are disabled. Codex
runs in a temporary directory with a read-only sandbox and ignores user CLI config. This does not expose Minecraft tools to a persistent CLI agent.

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
| `RECOVERY_DECISION_TIMEOUT` | `60` | Seconds per recovery decision; the earlier CLI/recovery deadline wins |
| `MC_ADAPTIVE_NAVIGATION` | `false` | Opt-in experimental mining navigation; recovery supervision is always enabled |
| `CODEX_CLI_PATH` / `CLAUDE_CLI_PATH` | PATH lookup | Optional executable path |

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

## Architecture

```text
Minecraft server ↔ Mineflayer bot ↔ Python agent ↔ Codex / Claude Code CLI
                              WebSocket :3001

Dashboard :3002 → Backend API :8000 → Coordinator :3010 / state files / PostgreSQL
```

- `agent/bot/`: game connection, movement, gathering, activity stack, and stall detection.
- `agent/brain/`, `agent/skills/`: CLI adapters and planning skills.
- `agent/executor.py`, `agent/recovery.py`: plan execution, result verification, and bounded recovery retries; waits for player help when needed.
- `agent/task_memory.py`: task goals, execution progress, and recent events.
- `backend/`: public API; the dashboard serves the page and fetches data from the backend.

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
