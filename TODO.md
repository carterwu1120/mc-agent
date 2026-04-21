# Minecraft Bot — Roadmap

> 標記 **[Backend]** 的項目對應真實的 backend / system design 概念。

## 進行中 / 近期

- [x] **Pydantic schema validation（LLM 輸出驗證）**
  - 目標：消滅 `json.loads` 後才炸的 KeyError，改在 LLM 層就驗收格式
  - [x] 在 `planner.py` 定義 `PlanResponse(BaseModel)`，用 `model_validate_json` 取代 `json.loads`
  - [x] 加 `reasoning` optional 欄位（model 說明為什麼這樣規劃）
  - [x] 推廣到所有 skill — 統一 `BaseLLMResponse` + `parse_llm_json` (`skills/llm_response.py`)

- [x] **Stuck recovery context 強化**
  - [x] `chopping/no_trees` + `nearby.trees=false` → deterministic shortcut，不走 LLM
  - [x] Y < 40 地底規則：prompt 注入 surface 前置約束，LLM 不可直接回 explore
  - [x] 傳入 `startPos` vs `currentPos` 距離差，讓 LLM 判斷是否真的在移動

- [x] **Coordinator agent（基本版）**
  - [x] `agent/skills/coordinator.py` — LLM 調度員，讀取所有 bot 狀態，智慧分配任務
  - [x] `@coord <request>` chat prefix → bot0 (COORDINATOR_BOT=true) 觸發調度流程
  - [x] 其他 bot 透過 `command_queue.json` 接收指派任務，idle 時自動執行
  - [x] dashboard `/state` 顯示 coordinator 各 bot 狀態摘要
  - 目前用 file-based IPC（`command_queue.json`）→ 升級見中期「Coordinator HTTP Service」

- [ ] **Multi-agent routing 收尾**
  - [x] `@BotName` / `@all` chat addressing
  - [x] bot 側忽略其他 bot 的聊天（`BOT_USERNAMES`）
  - [x] 每個 bot 獨立 data dir（`BOT_DATA_DIR`）、獨立 WebSocket port（`BOT_WS_PORT`）
  - [x] dashboard 聚合所有 bot 狀態（`live_state.json` polling）
  - [x] docker-compose 支援 bot0 + bot1 雙服務
  - [ ] 啟動 log 印出 `MC_USERNAME` / `BOT_USERNAMES` / `STRICT_CHAT_ADDRESSING`，方便確認生效設定
  - [ ] 統一 system chat / server announcement 過濾，避免 `Teleported ...` 被送進 planner

- [ ] **Manual override / interrupt 機制**
  - [ ] 自然語言 interrupt / resume 分類（不只靠前綴）
  - [ ] executor / stuck recovery 能接受人工覆蓋，避免舊流程在背景等待

- [ ] **通用 tool acquisition policy**
  - 目標：`mining` / `woodcutting` / `combat` 不各自實作 `ensurePickaxe` / `ensureAxe` / `ensureSword`
  - [ ] 共享 retry cooldown：上次 craft 失敗的 resource fingerprint，inventory 有哪些變化才允許重試
  - [ ] 統一 fallback：可徒手繼續的先繼續；不可徒手才升級成 replan

- [ ] **Python 側 context 清理機制 v2**
  - [x] v1：`context_builder` 對 recent events / failures / interrupted tasks / chests 做截斷與摘要
  - [x] v1：planner / self_task 改走共用 builder
  - [ ] v2：activity_stuck / verify_failure / 其他 skill 也統一接到共用 context builder
  - [ ] v2：加入重複事件折疊、按 skill 類型設定 context budget

- [ ] **Structured Logging** `[Backend: Observability]`
  - 為什麼需要：現在 log 是純文字，跨 service 難以追蹤問題根源。加上 `task_id` 後，
    一個任務從 coordinator 派出 → agent 接收 → bot 執行的整條流程都能串起來查。
  - [ ] log 改成 JSON 格式（`{"time", "level", "task_id", "service", "msg"}`）
  - [ ] coordinator / agent / executor 的操作都帶同一個 `task_id`
  - [ ] dashboard `/events?task_id=xxx` 可查整條 trace

---

## 中期

- [ ] **Goal-level verification（任務目標驗證）**
  - 目標：plan 全部 steps 跑完後，驗證是否真的達成 goal，而不只是 steps 跑完就算 done
  - 現有的 `_verify_step` 是 step-level（equip/smelt/mine 個別確認），缺 goal-level
  - [ ] `PlanExecutor` plan 完成後，拿 `goal` + before/after inventory snapshot 做 goal 驗收
  - [ ] 驗收失敗 → replan（優先 deterministic，不一定需要 LLM）
  - [ ] `task_memory` status `done` 改為區分 `completed`（goal 達成）vs `finished`（steps 跑完）

- [ ] **Plan reasoning 欄位推廣與驗證**
  - [ ] `reasoning` vs `commands` 一致性檢查（說要補鐵但 commands 沒有 smelt → 抓邏輯錯）
  - [ ] reasoning 可選擇性 chat 給玩家看（透明度）
  - [ ] 評估 Gemini 2.5 Flash vs Ollama 小模型的 reasoning 品質差距

- [ ] **Memory roadmap**
  - [ ] **Spatial memory 強化**（`exploration_memory.json` 已有基礎）
    - [x] 記錄 ore_finds / forest_finds / animal_areas
    - [x] self_task 讀取記憶優先去已知位置
    - [ ] 補 explored_chunks / 區域密度，讓 explore 不只記點，也記地圖覆蓋狀態
    - [ ] 記錄已知工作點（礦坑入口 / 熔爐位置 / 常用補給點）
  - [ ] **Task history**（SQLite 已有基礎）
    - `task.json` 只維持短期工作記憶；長期完整歷史已存 SQLite
  - [ ] **Interaction memory**（玩家偏好、長期目標、open threads）
  - [ ] **Reflection memory**（failure patterns、有效策略、bot 主動建議）

- [ ] **強化 self_task 自主規劃**
  - [ ] 目標分解下沉：把 equipment/tool 缺口推算擴展成通用系統層目標分解
  - [ ] 資源導向規劃：缺資源時先查 spatial memory，再決定是否 explore
  - [ ] deterministic 選點策略：多個已知資源點時定義最近 / 最新 / 最可信的選擇規則

- [ ] **Coordinator HTTP Service** `[Backend: Service-to-service communication / REST API design]`
  - 為什麼需要：現在用 `command_queue.json` 做 IPC — 只能單機、需要 polling、
    兩個 process 同時寫可能衝突。改成 HTTP API 後 coordinator 是唯一的 task owner，
    agent 透過 REST 接任務，不需要共享檔案系統。
  - [ ] coordinator 獨立成 aiohttp service（port 3010）
  - [ ] `POST /bots/register` — agent 啟動時向 coordinator 登記
  - [ ] `POST /bots/{id}/tasks` — 指派任務（body 帶 `task_id` 作為 idempotency key）
  - [ ] `PATCH /bots/{id}/tasks/{task_id}` — agent 回報任務進度 / 完成
  - [ ] `GET  /bots/{id}/state` — 查詢 bot 狀態（取代 `live_state.json` polling）
  - [ ] 移除 `command_queue.json` file polling

- [ ] **Heartbeat / Health Check** `[Backend: Reliability — failure detection]`
  - 為什麼需要：現在 coordinator 不知道 bot 是否還活著。Bot crash 後，
    coordinator 會永遠等它完成任務。Heartbeat 讓系統在 N 秒內偵測斷線並重新分配任務。
  - [ ] agent 每 10s 打 `POST /bots/{id}/heartbeat`
  - [ ] coordinator 超過 30s 沒收到 → 標記該 bot `offline`
  - [ ] offline bot 的 pending task 重新放回 queue（搭配 Task Queue 一起做）

- [ ] **Task Queue** `[Backend: Async decoupling — producer/consumer pattern]`
  - 為什麼需要：現在 coordinator 直接寫任務給某個 bot（同步 push）。
    改成 queue 後兩邊解耦：coordinator 只管「放進去」，bot 只管「拿出來執行」。
    Bot 忙碌時任務排隊等待，重連後自動繼續，任務不遺失。
  - [ ] in-memory queue per bot（`asyncio.Queue`）
  - [ ] task 有完整 lifecycle：`queued → running → done / failed`
  - [ ] bot 執行完後 ack，coordinator 才從 queue 移除（at-least-once delivery）

- [ ] **Rate Limiting（LLM 請求流量控制）** `[Backend: API stability / token bucket]`
  - 為什麼需要：多個 bot 同時卡住時，可能在短時間打出大量 LLM request 超過 API quota。
    Token bucket 讓系統在壓力下仍然穩定，超過限制時 queue 等待而不是直接報錯。
  - [ ] LLM client 層加 token bucket rate limiter（每分鐘最多 N 次）
  - [ ] 超過限制時 exponential backoff 等待，不拋例外

---

## 已完成

- [x] **Vertex AI provider（VertexClient）**
  - `agent/brain/vertex.py`：用 ADC 認證（`gcloud auth application-default login`），不需要 API key
  - `LLM_PROVIDER=vertex` 切換；`GOOGLE_CLOUD_PROJECT` / `GOOGLE_CLOUD_LOCATION` env vars
  - docker-compose mount `~/.config/gcloud` 進 container

- [x] **Multi-agent 基礎建設**
  - per-bot data isolation（`BOT_DATA_DIR` env var）
  - per-bot WebSocket port（`BOT_WS_PORT` env var）
  - `@BotName` / `@all` chat addressing（只有被點名的 bot 回應）
  - bot-to-bot feedback loop 封鎖（`BOT_USERNAMES` 忽略清單）
  - dashboard 多 bot 聚合（`live_state.json` 機制）
  - docker-compose bot0 + agent0 + bot1 + agent1 四服務

- [x] **Dashboard**（agent observability）
  - `agent/dashboard.py`：aiohttp HTTP server，port 3002
  - `agent/dashboard.html`：暗色主題單檔 UI，每 2 秒 polling `/state`
  - Multi-agent ready schema：`{ coordinator: null, agents: [{...}] }`

- [x] **Post-action verification loop**
  - `executor.py` 實作 `_verify_step()`，equip/smelt/mine/deposit 完成後比對 before/after state
  - 驗證失敗 → `_handle_verify_failure()` → 觸發 LLM 決策（replan / skip / accept）
  - LLM 不回 replan/skip 時自動 resume

- [x] **Deterministic rules 下沉到系統層**
  - `_enforce_pending_steps` — replan 缺 pending_steps 時自動補上
  - `_filter_done_steps_from_replan` — 移除 replan 開頭已完成的步驟
  - `_deduplicate_adjacent_cmds` — 移除連續重複指令
  - `_block_invalid_skip` — `_CRITICAL_DEPENDENCY_PAIRS` table 驅動的非法 skip 攔截

- [x] **Task memory 補強**
  - `interruptedTasks`、`recentEvents`、`recentFailures`、`recentTransitions`，附 TTL/cap prune
  - `final_goal` 欄位跨任務繼承最終目標
  - `load_any()` 讓 planner 看到所有狀態的任務

- [x] **SQLite task history（history_db.py）**
  - `tasks` / `events` / `failures` 三張表
  - dashboard `/history` `/failures` `/events` endpoint

- [x] **context_builder v1**
  - 抽出共用 `context_builder.py`，planner / self_task 改走共用 builder

- [x] **activity_stuck.py 重構成 `skills/stuck/` 分目錄**
  - 拆出 smelting / mining / hunting / getfood 子模組
  - decision 驗證、LLM utils、prompt builder 各自獨立

- [x] **修正 mining 無鎬時 push/pop smelting tight loop**
- [x] **修正 equip 指令 action_done 遺漏**
- [x] **修正 planner 對 equip 的濫用**
- [x] **`!test verify_failure` + `test_plan` bridge event**
- [x] Activity stack LIFO 架構
- [x] PlanExecutor + task_memory 基礎版
- [x] 背包整理（inventory_full LLM 決策）
- [x] 箱子自動化（makechest + labelchest + deposit，含 `{new_chest_id}` placeholder）
- [x] 裝備耐久監控 + 背包滿攔截
- [x] 復活後恢復任務
- [x] self_task workflow mode 自動恢復中斷任務
- [x] Spatial memory（exploration_memory.json）接入 self_task
