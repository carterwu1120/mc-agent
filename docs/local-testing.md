# 本機連線與挖礦導航測試

## 單人世界和獨立伺服器的差別

- **單人世界開 LAN**：你先開 Java 單人存檔，再開放至區域網路。遊戲暫時提供一個 server，bot 用 host/port 加入；你退出存檔，連線也會結束。適合回家先測。
- **獨立伺服器**：另外啟動 Minecraft server，你與 bot 都是玩家。它可以在你沒開遊戲時繼續運行，也可讓更多人加入。bot 的行動程式不需要因此重寫。

本專案使用 Mineflayer，這份流程以 **Java Edition** 為準。現有預設版本是 **1.20.1**；請使用實際遊戲/伺服器版本。Bedrock 不是這份直接連線流程。

## 現在不用開遊戲也能做的事

在專案根目錄的 PowerShell：

```powershell
node --version # 需要 Node.js 22 或以上
uv sync --group dev
npm.cmd ci --prefix agent/bot
npm.cmd test --prefix agent/bot
```

JS 測試不會啟動遊戲或呼叫模型。地形測試使用真正的 Pathfinder 規劃器與合成方塊資料，動作執行則是模擬的。因此它能驗證繞路、挖路、搭路、失敗記憶、取消與 stack 恢復；無法驗證遊戲內碰撞、網路延遲、伺服器是否允許放置、模組方塊或 mobs。

## 回家：先連進原本的單人世界

1. 開啟 Minecraft **Java Edition**，進入單人存檔。
2. 按 `Esc` → **開放至區域網路 / Open to LAN** → 開始 LAN 世界。若要使用這個專案的 `tp` 等指令，世界也需要允許指令；單純移動與挖掘不依賴它。
3. 記下遊戲顯示的 **port**。1.20.1 的 LAN port 通常每次重新開放都可能不同，不能一律填 `25565`。
4. 在專案根目錄建立 `.env`（已存在就編輯，不覆蓋）：

```powershell
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
```

例如你與 bot 都在同一台電腦，LAN port 是 `51234`：

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
```

`51234` 是示例，要換成遊戲剛顯示的 port。`MC_FORGE=false` 適用原版；若你原本用 Forge 再設 `true`。Forge 握手本身不保證所有模組相容。

`offline` 只適用接受 offline 登入的 LAN/測試伺服器。若伺服器要求正版驗證，改用 `MC_AUTH=microsoft`，`MC_USERNAME` 填 bot 帳號的登入識別（通常是 email），啟動時依提示用瀏覽器登入。你與 bot 要同時在線時，使用不同、具備 Java 遊戲資格的帳號；聊天中要 @ 的是 **bot 實際遊戲名稱**，不是 email。

先檢查設定，不連線：

```powershell
npm.cmd run check --prefix agent/bot
```

第一個終端機，啟動身體（會連進世界）：

```powershell
node agent/bot/index.js
```

看到 `[Bot] 進入世界！` 即表示 Minecraft 連線成功。此時不需要 Python 或 AI 就能在遊戲聊天測直接指令：

```text
@Agent !setmode companion
@Agent !mine stone 3
@Agent !stopmine
```

`companion` 模式可避免 Python 閒置時自行接新任務，便於先測行動。`!` 指令直接進 JS，能把腳本/地形問題與模型規劃問題分開觀察。

第二個終端機，同樣從專案根目錄啟動大腦：

```powershell
# 先登入選定 CLI：Codex 使用 codex.cmd login；Claude 執行 claude 後選訂閱登入。
uv run -m agent.agent
```

接著在遊戲說：`@Agent 幫我挖 3 個鐵`。本機最小測試不需要設定 PostgreSQL 或 coordinator；完整歷史查詢與 dashboard 資料 API 則需要 backend/資料庫服務。

JS 現在會自動讀專案根目錄 `.env`，且不覆蓋終端機已設定的環境變數。改 port 或導航開關後，需要重新啟動 bot。

## 使用 Docker 時

若遊戲開在 Windows 主機、bot 跑在 Docker，改成：

```dotenv
MC_HOST=host.docker.internal
MC_PORT=51234
MC_USERNAME_0=Agent0
MC_FORGE=false
MC_ADAPTIVE_NAVIGATION=true
```

容器裡的 `localhost` 是容器自己，不能用來指向主機的 LAN 世界。Compose 使用 `MC_USERNAME_0` 設定第一隻 bot 的名稱，所以聊天要用 `@Agent0`。CLI 在容器裡也需要另外登入；完整流程見 [README](../README.md#docker-compose)。

## 回家驗收導航

新導航是 **挖礦試點**，以 `MC_ADAPTIVE_NAVIGATION=true` 啟用；設 `false` 並重啟可比較舊方式。它尚未取代原有階梯下降、開隧道與逃生腳本的全部邏輯。

先在簡單、沒有怪物干擾的測試區域，讓 bot 帶鎬與少量 cobblestone：

| 情境 | 預期結果 |
|---|---|
| 目標前有牆、旁邊可走 | 產生繞路路線 |
| 狹窄通道被可挖方塊擋住 | 改用允許挖掘的路線 |
| 小缺口，有足夠方塊與放置依附面 | 嘗試包含放置的路線 |
| 缺材料或沒有可用路線 | 有限嘗試後回報 blocked，不無限跑同一方法 |
| 挖礦中斷後在新位置恢復 | 舊執行被取消，從新位置重新規劃 |

合成地形測試不代表以上情境已經通過遊戲內驗收。回家請記錄可重現地形、指令、背包、終端機訊息與遊戲版本。最近導航結果也會出現在 JS→Python 的 `navigation` 狀態欄位，並加入卡住處理的 prompt。

失敗記憶保存在目前 bot 程序，最多 128 筆、兩分鐘後過期；位置、目標附近地形或背包改變可重新評估。重啟程序會清空。這是第一階段的局部防重試機制，不是完整世界記憶。

## 常見連線問題

| 訊息/現象 | 先檢查 |
|---|---|
| `ECONNREFUSED` | 世界是否已開 LAN、port 是否仍是這次的 port、host 是否正確 |
| 版本不相容 | `MC_VERSION` 是否符合 server；可用 `auto` 嘗試自動偵測 |
| Invalid session / 驗證失敗 | 是否需要 `MC_AUTH=microsoft`、是否用了與玩家相同的帳號 |
| 跨電腦連線逾時 | 填的是遊戲主機的 LAN IP，且防火牆允許該連線 |
| bot 在世界裡但不理自然語言 | Python 是否連上 WebSocket、CLI 是否登入、聊天是否用了正確 @名稱 |

官方參考：[Minecraft LAN/server 操作](https://www.minecraft.net/en-us/article/how-play-minecraft-server)、[Mineflayer 連線與 Microsoft 認證](https://github.com/PrismarineJS/mineflayer)。

## 停滯與異常閒置恢復測試

恢復監控預設啟用，與 `MC_ADAPTIVE_NAVIGATION` 的尋路開關分開。
離線驗證：

```powershell
npm.cmd test --prefix agent/bot
uv run --group dev pytest test/test_recovery.py test/test_executor_liveness.py test/test_recovery_integration.py -q
```

回家進遊戲後，優先觀察以下情況：

- 原地卡住或兩點間來回：約 25 秒沒有新位置／產出會暫停，接著等 AI 新策略。
- 指令沒有啟動卻持續有心跳：未完成採集任務 idle 約 15 秒觸發恢復；裝備／箱子等一次性操作最多等 45 秒。
- 有移動卻沒取得目標資源：約 180 秒要求策略檢討；挖鑽石時持續取得圓石不會永久掩蓋問題。
- 釣魚／熔煉的合理等待：有期限的等待應正常完成；到期仍沒有結果才處理。
- AI 決策途中手動停止或改任務：晚到的舊答案和完成事件不能重新啟動舊工作。
- CLI 失敗、回傳無效格式或只聊天：最多兩次嘗試，之後保留任務並說明等待協助的原因。

`RECOVERY_DECISION_TIMEOUT=60` 是單次恢復決策的秒數，可依實際 CLI 延遲調整。
`CLI_AGENT_TIMEOUT` 仍是 provider 的上限，兩者先到者生效。一次恢復最多兩次決策，
五分鐘內最多三輪恢復，新的明確任務會重設。這是呼叫次數限制，不是精確 token 配額。
若收到「暫停等待協助」，改善條件後重新給指令或說繼續，不會默默無限重試。

查看 `[Recovery]` 日誌與 `agent/data/live_state.json` 的 `supervision`：
`stopping`、`waiting_ai`、`executing`、`waiting_player`，以及剩餘秒數、預期結果和阻礙原因。
停止確認只證明本地控制流程已撤銷，不能撤回已送到伺服器的放置／挖掘封包。
真實地形、伺服器延遲與其他插件操作，仍需遊戲測試確認。
