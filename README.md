# Minecraft LLM Agent

以 Mineflayer 控制 Minecraft bot，透過 Codex 或 Claude Code CLI 規劃任務。
支援多步驟執行、背包管理與有限次數的卡住恢復；模型決策與遊戲內行動仍需實測驗證。

本機測試固定使用 **Minecraft Java Edition 1.20.1 + Java 17**。
先依下方建立測試伺服器，再啟動 bot；也可使用單人世界開 LAN。
連線排錯與導航／恢復驗收見 [本機測試指南](docs/local-testing.md)。

## Local Development

### 固定版本的本機測試伺服器（Vanilla 1.20.1）

若要使用固定 port 的獨立測試世界，可用此流程取代下方的「單人世界開 LAN」。
遊戲客戶端、伺服器與 bot 統一使用 **Minecraft Java Edition 1.20.1**；
伺服器使用 **Java 17**，可先執行 `java -version` 確認。

1. 到 [Minecraft 官方 1.20.1 發布頁](https://www.minecraft.net/en-us/article/minecraft--java-edition-1-20-1)，
   點選 **Cross-platform server jar** 下的 **Minecraft server jar**。
2. 在專案根目錄建立 `local-server/`，將下載的檔案放成 `local-server/server.jar`。
3. 在專案根目錄開啟 PowerShell，執行：

```powershell
cd local-server
java -Xms1G -Xmx2G -jar server.jar nogui
```

首次啟動會解壓縮依賴、產生設定檔，並因尚未同意 EULA 而停止。
閱讀 `eula.txt` 中的條款連結，同意後自行將 `eula=false` 改成 `eula=true`，
再執行相同的 Java 指令。看到 `Done` 表示伺服器已就緒。
保持終端機開啟；結束時輸入 `stop`，讓世界正常儲存。

用 **1.20.1** 遊戲客戶端選擇「多人遊戲 → 直接連線」，輸入 `localhost:25565`。
若同一台電腦還有其他使用 `25565` 的伺服器，請先關閉它。
專案根目錄 `.env` 的連線設定為：

```dotenv
MC_HOST=127.0.0.1
MC_PORT=25565
MC_VERSION=1.20.1
MC_FORGE=false
```

登入方式必須配合 `local-server/server.properties`：

- 預設 `online-mode=true`：bot 使用 `MC_AUTH=microsoft`，並以另一個具備 Java 遊戲資格的帳號登入；
  `MC_USERNAME` 填該帳號的登入識別，通常是 email。
- 僅供同機離線測試：關服後設 `online-mode=false`、`server-ip=127.0.0.1`，重啟伺服器；
  bot 使用 `MC_AUTH=offline`、`MC_USERNAME=Agent`。此設定只接受同機連線，不適用朋友或虛擬機連線。

接著依下方快速啟動流程安裝依賴、啟動 JS bot 與 Python agent；略過開 LAN 與 LAN port 設定。

**Git 只保存重建環境的說明。** `.gitignore` 排除整個 `/local-server/`，
包括伺服器 JAR、依賴、EULA、本機設定、日誌、玩家資料與世界存檔。
每台電腦自行下載指定版本並接受 EULA；不將伺服器程式或存檔提交至 Git。

<a id="windows-local-quick-start"></a>

### Windows 本機快速啟動（單人世界開 LAN）

以下指令都在專案根目錄的 PowerShell 執行。需要 **Node.js 22+**、
**Python 3.10–3.12**、**uv**，以及已安裝、登入訂閱帳號的 Codex 或 Claude Code CLI。

1. 安裝專案依賴並建立設定檔；已有 `.env` 時保留原檔：

```powershell
uv sync --group dev
npm.cmd ci --prefix agent/bot
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

2. 開 Minecraft **Java Edition** 單人世界 → `Esc` → **開放至區域網路**，記下顯示的 port。
   編輯的是 **`.env`**，不是 `.env.example`。遊戲與 bot 同一台電腦時，例如：

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

`51234` 要換成這次 LAN 的 port，版本也要符合實際世界。`MC_AUTH=offline`
只適用接受 offline 登入的環境；需要正版驗證時，請依
[登入設定](docs/local-testing.md#回家先連進原本的單人世界)改用 `microsoft` 與獨立 bot 帳號。
使用 Claude Code 時把 `LLM_PROVIDER` 改成 `claude`。

`MC_ADAPTIVE_NAVIGATION=true` 啟用實驗性的挖礦導航；卡住／閒置恢復監控預設就有，
不依賴這個開關。改設定後需重啟對應程序。

3. 第一個終端機檢查設定、啟動身體：

```powershell
npm.cmd run check --prefix agent/bot
node agent/bot/index.js
```

4. 第二個終端機啟動大腦（CLI 登入方式見下方 [planning providers](#codex--claude-code-planning-providers)）：

```powershell
uv run -m agent.agent
```

5. 遊戲聊天先切成陪伴模式，避免閒置時自行接任務，再下測試指令：

```text
@Agent !setmode companion
@Agent 幫我挖 3 個鐵
```

`@Agent` 必須換成 bot 的實際遊戲名稱。直接測腳本可用 `@Agent !mine stone 3`，
停止挖礦用 `@Agent !stopmine`。結束測試時，在兩個終端機各按 `Ctrl+C`。

本機最小測試不需要設定 `DATABASE_URL`、`COORDINATOR_PORT` 或 `COORDINATOR_URL`。
完整 dashboard 資料與歷史查詢需要 backend 和 PostgreSQL；想一次啟動完整服務，
使用下方 [Docker Compose](#docker-compose) 流程。Docker 連主機遊戲時
`MC_HOST=host.docker.internal`，名稱用 `MC_USERNAME_0=Agent0`，聊天則用 `@Agent0`。

離線測試、連線排錯與回家驗收清單見 [本機連線與挖礦導航測試](docs/local-testing.md)。

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

在專案根目錄執行（依賴安裝見上方快速啟動）：

```powershell
uv run --group dev pytest test -q
npm.cmd test --prefix agent/bot
```

只測 CLI provider 轉接器：

```powershell
uv run --group dev pytest test/test_cli_agent.py -q
```

CLI provider 測試使用模擬 CLI，不需要登入或呼叫模型；JS 測試不會啟動 Minecraft。
自動測試不能取代真實登入、伺服器連線與遊戲內操作驗收。

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

- `agent/bot/`：遊戲連線、移動、採集、活動堆疊與停滯偵測。
- `agent/brain/`、`agent/skills/`：CLI 轉接器與規劃技能。
- `agent/executor.py`、`agent/recovery.py`：依序執行計畫、驗證結果、限制恢復重試；必要時等待玩家協助。
- `agent/task_memory.py`：保留任務目標、執行進度與近期事件。
- `backend/`：公開 API；dashboard 只提供頁面，資料由 backend 取得。

多 bot 使用獨立 `BOT_ID` 與資料目錄，透過 coordinator 註冊及領取任務。
聊天以 `@Agent0`、`@Agent1` 指定對象；`BOT_USERNAMES` 用來忽略其他 bot 的聊天。

## Backend API

完整端點、請求與回應格式見 [Backend API Contract](docs/backend-api.md)。
使用完整 Docker stack 時，可先檢查：

```powershell
curl.exe http://localhost:8000/health
curl.exe http://localhost:8000/ready
curl.exe http://localhost:8000/bots
```

Dashboard：<http://localhost:3002>；公開 API：<http://localhost:8000>。
Coordinator 的 `3010` port 為內部服務，不作為用戶端 API。

## Known limitations

- 每次模型決策都啟動 CLI 子程序，會有延遲，也受訂閱使用量限制。
- Forge 握手不代表完整模組相容；新增方塊、物品或封包需另外驗證。
- `MC_ADAPTIVE_NAVIGATION` 是實驗性挖礦導航，合成地形測試不能保證所有實際地形都能通過。
- 多 bot 尚可能競爭同一資源；目前沒有跨工作階段的完整世界地圖。
