# Minecraft Bot — Roadmap

> 標記 **[Backend]** 的項目對應真實的 backend / system design / platform engineering 練習。

## 三階段主線

### Phase 1（現在）— Minecraft Agent → Backend Service

目標：把目前偏 runtime-oriented 的 agent 專案，整理成有明確 control/query boundary 的 backend service。

技術重點：
- FastAPI
- REST API design
- DB integration
- Docker / docker compose
- async service boundaries

完成定義：
- 有單一 public backend API，外部不需要直接碰 agent 內部模組
- task / bot / metrics 有穩定 JSON schema
- dashboard / coordinator / history query 不再是分散的 ad hoc endpoint
- `docker compose up` 可啟動固定數量長駐 bot + backend

#### 1.1 API / Service Boundary `[Backend: Service architecture]`

- [x] **FastAPI control/query layer（MVP）**
  - [x] 建立獨立 backend service，作為 public JSON API 入口
  - [x] `POST /bots/{bot_id}/tasks`
  - [x] `GET /bots`
  - [x] `GET /bots/{bot_id}/state`
  - [x] `GET /bots/{bot_id}/tasks`
  - [x] `GET /tasks/{task_id}`
  - [x] `GET /metrics/success-rate`
  - [x] `GET /metrics/stuck-count`
  - [x] `GET /metrics/llm-latency`

- [x] **統一 API ownership，避免重複 surface**
  - [x] 決定 `dashboard.py` 先保留，backend 逐步吸收 query responsibility
  - [x] 決定 `coordinator_service.py` 先作為 internal service，由 backend facade 包起來
  - [x] 對外只保留一套 public API 命名與 response schema（dashboard query routes 已收掉，dashboard 改走 backend）

- [~] **Bot / Task state schema 正規化**
  - [x] 定義 bot summary schema（id、status、activity、position、health、food、current_task）
  - [x] 定義 task schema（queued / running / done / failed / interrupted）
  - [x] 明確區分 runtime state、task history、aggregated metrics
  - [x] 補 task source / interrupt / abort / heartbeat 的 API model
  - [x] 補一份 canonical API schema 文件（欄位表 + example payload），固定 `GET /bots`、`GET /bots/{bot_id}/state`、`GET /tasks/{task_id}` 的 response contract

#### 1.2 Runtime Control / Reliability

- [x] **Manual override / interrupt 機制**
  - [x] 自然語言 interrupt / resume 分類（縮減 regex，交給 task_arbitration LLM 仲裁；補充英文輸入規則）
  - [x] executor / stuck recovery 能接受人工覆蓋（abort() 已有 _run_id + _done.set() 機制，舊 coroutine 立即退出）
  - [x] backend API 可觸發 abort / interrupt / resume
  - [x] 明確定義 public control endpoints（`POST /bots/{id}/abort`、`POST /bots/{id}/resume`、`POST /bots/{id}/tasks` with goal-only）
  - [x] POST /bots/{id}/tasks 支援純自然語言 goal（agent planner 自動規劃 commands）

- [x] **Coordinator state 可查詢化**
  - [x] in-memory queue / running task / interrupt slot / abort flag 有明確 query path
  - [x] `GET /tasks/{task_id}` 可同時看見 queued / running / archived 狀態
  - [x] heartbeat / offline bot 狀態可從 API 查詢，不只看 log

- [ ] **通用 tool acquisition policy**
  - 目標：`mining` / `woodcutting` / `combat` 不各自實作 `ensurePickaxe` / `ensureAxe` / `ensureSword`
  - [~] 共享 retry cooldown：resource fingerprint 防止 inventory 未變化時重複觸發同一 replan（`git stash`：`codex: tool_acquisition fingerprint + smelt alias canonicalization`）
  - [ ] 統一 fallback：可徒手繼續的先繼續；不可徒手才升級成 replan

#### 1.3 Data Layer `[Backend: SQL / Schema Design]`

- [x] **DB access layer**
  - [x] 把現在散在 dashboard / history query 的 DB 存取整理成 repository/service layer
  - [x] 把 metrics query 從 route handler 中抽離，讓 FastAPI 可直接重用
  - [x] 規劃 task / event / failure / logs 的 API-facing DTO

- [x] **SQLite → PostgreSQL 遷移**
  - [x] 先完成 schema 抽象與 repository 介面，避免 route 直接綁 SQLite
  - [x] 將現有 `task_history.db`（tasks / events / failures / logs）遷移至 PostgreSQL
  - [x] 設計正規化 schema，補上 index（task_id、bot_id、created_at、event_type）
  - [x] 多 bot 共用同一 DB，以 `bot_id` 做資料隔離，取代現在的 per-bot 檔案分離
  - [x] 練習 migration script、connection pool（asyncpg）、transaction 管理

- [~] **Metrics 補齊**
  - [x] LLM latency API：從 `llm_call` event 聚合 avg / p95 / count
  - [x] stuck count API：可依 activity / reason 聚合
  - [x] success rate API：區分 task done 與 goal verified
  - [x] metrics query 補 bot_id filter / narrower query capability
  - [x] Goal completion rate：`activity_done` 時記錄實際完成量 vs 目標量，區分「程式跑完」vs「目標達成」
    - [x] JS activity_done payload 統一帶 goal + progress（sendActivityDone helper）
    - [x] DB schema 加 goal_count / actual_count 欄位
    - [x] task_memory.done() 寫入 goal_count / actual_count
    - [x] metrics API：GET /metrics/goal-completion endpoint

#### 1.4 Async / Deployment / Testing

- [x] **Docker / compose service layout**
  - [x] `backend` 服務獨立於 `agent0`
  - [x] backend 與 coordinator / agents 的 network / volume 邊界明確化
  - [x] 保持 MVP 為 1 到 2 個 long-running bots，不做動態 spawn container
  - [x] compose 補 service healthcheck（backend `/health` / `/ready`）

- [x] **Coordinator queue poll unblocked from LLM**
  - [x] 將 coordinator queue poll 改為 `asyncio.create_task`（不被 `_thinking` 擋住）
  - [x] 使用 `_planner_lock` 統一 executor ownership，取代分散的 flag 機制
  - [x] 使用者給任務時，self_task LLM 結果被丟棄，改由 coordinator 任務接手
  - [ ] **Known concurrency issues（future work）：**
    - [ ] Chat / coordinator_interrupt 路徑尚未納入 `_planner_lock`，理論上仍有 race
    - [ ] Lock 在 `create_task` 後即釋放，executor `_running=True` 尚未設定，存在短暫 gap
    - [ ] WebSocket 斷線時 `_check_coordinator_queue` coroutine 未加入 `session_tasks`，不會被 cancel
    - [ ] Coordinator task dequeue 後若因狀態衝突跳過，task 會留在 `running` 狀態（無 requeue）

- [ ] **Python 側 context 清理機制 v2**
  - [ ] v2：activity_stuck / verify_failure / 其他 skill 也統一接到共用 context builder
  - [ ] v2：加入重複事件折疊、按 skill 類型設定 context budget

- [~] **Replay testing（regression tests）** `[Backend: Testing]`
  - [ ] 從 production stuck state 建 unit test fixtures（state dict → expected commands）
  - [ ] 至少涵蓋：mining no_tools、smelting no_input、chopping no_trees
  - [x] 為 FastAPI route / repository layer 補 API tests

- [ ] **Plan reasoning 欄位推廣與驗證**
  - [ ] `reasoning` vs `commands` 一致性檢查（說要補鐵但 commands 沒有 smelt → 抓邏輯錯）
  - [ ] reasoning 可選擇性 chat 給玩家看（透明度）
  - [ ] 評估 Gemini 2.5 Flash vs Ollama 小模型的 reasoning 品質差距

### Phase 2 — RAG + Knowledge System

目標：把 Minecraft 世界知識、合成知識、經驗記憶從 prompt hardcode 轉成可查詢知識層。

技術重點：
- embeddings
- vector DB
- retrieval pipeline
- prompt integration / eval

完成定義：
- planner / activity_stuck / self_task 能按情境查知識，而不是全塞 prompt
- retrieval 有基本評估方法，不只是「感覺比較聰明」

#### 2.1 Knowledge Base / Retrieval

- [ ] **RAG / Knowledge Base**
  - [ ] 把 Minecraft 合成配方、生物分布、地形規則做成 vector store
  - [ ] LLM 需要相關知識時查詢，而不是全部塞進 prompt
  - [ ] 可先用 ChromaDB（embedded，無需另起服務）+ sentence-transformers
  - [ ] 適用情境：activity_stuck prompt 查「這個 ore 在哪一層」、planner 查合成配方

- [ ] **Retrieval pipeline**
  - [ ] query construction：從 task / stuck state 萃取查詢字串
  - [ ] retrieval + rerank 基本流程
  - [ ] context injection：只注入與當前 skill 相關的知識片段
  - [ ] fallback：查不到時退回原本 prompt 流程

#### 2.2 Memory roadmap

- [ ] **Spatial memory 強化**（`exploration_memory.json` 已有基礎）
  - [x] 記錄 ore_finds / forest_finds / animal_areas
  - [x] self_task 讀取記憶優先去已知位置
  - [ ] 補 explored_chunks / 區域密度，讓 explore 不只記點，也記地圖覆蓋狀態
  - [ ] 記錄已知工作點（礦坑入口 / 熔爐位置 / 常用補給點）

- [ ] **Task history / long-term memory**
  - `task.json` 只維持短期工作記憶；長期完整歷史由 DB 保存
  - [ ] Interaction memory（玩家偏好、長期目標、open threads）
  - [ ] Reflection memory（failure patterns、有效策略、bot 主動建議）

- [ ] **強化 self_task 自主規劃**
  - [ ] 目標分解下沉：把 equipment/tool 缺口推算擴展成通用系統層目標分解
  - [ ] 資源導向規劃：缺資源時先查 spatial memory，再決定是否 explore
  - [ ] deterministic 選點策略：多個已知資源點時定義最近 / 最新 / 最可信的選擇規則

#### 2.3 Evaluation

- [ ] 建立 retrieval eval 樣本（query → expected knowledge chunk / expected command improvement）
- [ ] 比較有無 retrieval 時的 stuck recovery 成功率
- [ ] 量測 prompt token 下降幅度與 latency 影響

### Phase 3 — Agent Platform 化

目標：從「能跑的多 agent side project」往「可觀測、可追蹤、可部署的平台雛形」前進。

技術重點：
- observability
- workflow orchestration
- tracing
- deployment

完成定義：
- 能回答「哪個 bot 卡住、卡在哪、哪個 skill 慢、哪條 workflow 常失敗」
- 系統重啟、擴充、排障有比較穩定的操作面

#### 3.1 Observability / Tracing `[Backend: Platform engineering]`

- [ ] **Tracing**
  - [ ] 為 task_id / bot_id / step_id 建立跨 service trace context
  - [ ] JS bot → Python agent → coordinator / backend 的 request chain 可串起來
  - [ ] 評估 OpenTelemetry 或至少自定 structured trace spans

- [ ] **Observability v2**
  - [ ] backend metrics：request latency、error rate、queue depth、bot heartbeat freshness
  - [ ] workflow metrics：plan length、replan count、verify fail count、interrupt count
  - [ ] dashboard / API 顯示 bot health timeline 與 task timeline

#### 3.2 Workflow Orchestration

- [ ] 抽象化 task workflow / state transition model
- [ ] 明確定義 queued / running / interrupted / resumed / failed / completed 狀態機
- [ ] 評估是否需要 workflow engine / job queue，或維持輕量自製 orchestrator
- [ ] 支援更清楚的 coordinator scheduling policy 與 retry policy

#### 3.3 Deployment / Platform Ops

- [ ] deployment profile：dev / local compose / remote host
- [ ] restart / recovery strategy：bot crash、agent crash、backend crash、DB crash
- [ ] config management：env var、bot-specific config、provider-specific config
- [ ] 後續若要擴充，再評估 dynamic bot provisioning / container lifecycle

## 每階段都要做的事

- [ ] **Evaluation / success criteria**
  - [ ] Phase 1：API correctness、task status consistency、task/metrics latency
  - [ ] Phase 2：retrieval quality、stuck recovery 改善、prompt size / latency tradeoff
  - [ ] Phase 3：traceability、MTTR、deploy / restart reliability

- [ ] **Documentation**
  - [ ] README architecture diagram 隨 phase 更新
  - [ ] 補 internal API docs / data model docs / runbook

## 已完成

- [x] **FastAPI backend service facade** `[Backend: Service architecture]`
  - 獨立 `backend` service + `Dockerfile.backend`
  - public JSON API：bots / tasks / metrics
  - coordinator internal read APIs：runtime task / queue / heartbeat visibility
  - shared state/history readers，避免 dashboard/backend 重複 shaping logic
  - coordinator task id 可一路查到 archived history

- [x] **Dashboard ownership cleanup**
  - dashboard server 降級成 UI-only，保留 `GET /`
  - dashboard frontend 改從 backend `:8000` 取資料
  - 移除 dashboard 舊 query surface（`/state`、`/history`、`/metrics` 等）
  - backend 補 CORS，支援 dashboard cross-origin fetch
  - backend 補 `/health`、`/ready`

- [x] **Rate Limiting（LLM 請求流量控制）** `[Backend: API stability / token bucket]`
  - Token bucket + exponential backoff 已實作在 `agent/brain/rate_limiter.py`

- [x] **Structured Logging + Observability（基礎版）** `[Backend: Observability]`
  - JS bot log 改成 JSONL（`{"time", "level", "service", "bot_id", "task_id", "msg"}`）
  - executor 每條指令帶 `_task_id`，JS logger 同步更新，bot/brain log 可用 task_id 串接
  - LLM call latency 記錄（`[LLM] ok latency=Xs`）
  - `GET /metrics?hours=N`：task 成功率、stuck by reason/activity
  - log 檔自動輪替（7 天後刪除）

- [x] **Coordinator HTTP Service + Task Queue + Heartbeat** `[Backend: Service-to-service / REST / Async decoupling / Reliability]`
  - `agent/coordinator_service.py`：aiohttp service（port 3010）
  - `POST /bots/register`（含 retry）、`POST /bots/{id}/tasks`（idempotency key）、`PATCH /bots/{id}/tasks/{task_id}`
  - in-memory queue per bot（`asyncio.Queue`）、task lifecycle：`queued → running → done / failed`
  - interrupt slot（`GET /bots/{id}/tasks/interrupt`）、abort flag（`POST/GET /bots/{id}/abort`）
  - 每 10s heartbeat、30s 無心跳 → drain queued tasks 標記 failed
  - `PYTHONUNBUFFERED=1` 加入 Dockerfile.agent

- [x] **Task source tracking + smart coordinator interruption**
  - task_memory 加 `source` 欄位：`player` / `self_task` / `coordinator` / `unknown`
  - coordinator 只中斷 `self_task`，不中斷 `player` / `coordinator` 任務
  - coordinator LLM 看到 source，決策更精準；abort 指令對應 `aborts` 欄位

- [x] **Coordinator agent（基本版 → HTTP 升級版）**
  - `agent/skills/coordinator.py`：LLM 調度員，讀取所有 bot 狀態，智慧分配任務
  - `@coord <request>` chat prefix 觸發調度
  - 同類型指令禁止分配給多個 bot、text 欄位需如實描述現況

- [x] **Pydantic schema validation（LLM 輸出驗證）**
  - 統一 `BaseLLMResponse` + `parse_llm_json`（`skills/llm_response.py`）
  - `reasoning` optional 欄位

- [x] **Stuck recovery context 強化**
  - `chopping/no_trees` deterministic shortcut
  - Y < 40 地底規則 prompt 注入
  - `startPos` vs `currentPos` 距離差傳入

- [x] **Multi-agent 基礎建設**
  - per-bot data isolation、per-bot WebSocket port
  - `@BotName` / `@all` chat addressing、bot-to-bot chat 封鎖
  - dashboard 多 bot 聚合、docker-compose 四服務

- [x] **Dashboard（基礎版）**
  - `agent/dashboard.py` + `agent/dashboard.html`
  - SQLite history endpoint
  - 多 bot 聚合 state schema

- [x] **Goal-level verification（任務目標驗證）**
  - `_verify_goal()`：plan 全部 steps 跑完後，用 plan 開始/結束 inventory snapshot 驗收最後一個 output 指令
  - 涵蓋：mine / smelt / chop / fish / hunt（嚴格比對目標數量，非只 > 0）
  - `_build_goal_remediation()`：動態計算 deficit，能補的就補（smelt 不夠 → 先 mine 再 smelt）；只重試一次（`_goal_retry` 參數）
  - `task_memory.done(goal_verified=bool)`：記錄 `goalVerified` 欄位

- [x] **Post-action verification loop**
  - `_verify_step()`：equip / smelt / mine / deposit / fish / hunt 完成後比對 before/after state
  - 驗證失敗 → LLM 決策（replan / skip / accept）

- [x] **Stability fixes（overnight run 診斷）**
  - cobblestone 改直挖（`_digStraightDown`），不走 tunnel 邏輯避免山地卡死
  - `_digTunnel` 加 `noProgressSteps` 計數器，連續 3 步無前進即放棄
  - inventory_full 與 activity 完成的 race condition（`waitUntilIdle` 防止 smelt 指令被丟棄）
  - executor 加 90s idle 偵測：activity 指令送出後若 JS 仍未啟動，自動重送一次
  - Dockerfile.agent 補 `2>>` stderr redirect，Python crash 可查 `agent/logs/stderr-*.log`

- [x] **Stuck loop fixes（production log 診斷）** `[Backend: Observability / Agent reliability]`
  - `mine iron` stuck `no_tools` + `can_make_pickaxe=False` → deterministic replan（chop → equip → mine stone → equip → retry）
  - `smelt raw_iron` stuck `no_input` → deterministic replan（mine ore → smelt）
  - `state_summary.py` `can_make_iron_pickaxe` 計入 `raw_iron`（equip 會自動冶煉）
  - `activity_stuck.py` 補上 smelting `no_input` caller gate（dead code fix）

- [x] **Eval metrics: stuck handler path tracking + LLM call logging** `[Backend: Observability]`
  - `activity_stuck.py`：每個 return 標記 `_path = deterministic | llm`，LLM path 計時
  - `agent.py`：提取 `_path` metadata，寫 `llm_call` event 到 DB；非 stuck handler 統一計時
  - `executor.py`：`replan()` / `skip_step()` 傳遞 `path_taken`，寫入 `events.details`
  - Replay testing：可用真實 stuck state 對 deterministic_shortcut 做 regression test
  - README 補 Evaluation section（SQL queries）

- [x] **Deterministic rules 下沉到系統層**
  - `_enforce_pending_steps`、`_filter_done_steps_from_replan`、`_deduplicate_adjacent_cmds`、`_block_invalid_skip`

- [x] **Vertex AI provider（VertexClient）**
  - ADC 認證、`LLM_PROVIDER=vertex`

- [x] **context_builder v1**、**Task memory 補強**、**SQLite task history**
- [x] **activity_stuck.py 重構成 `skills/stuck/` 分目錄**
- [x] Activity stack LIFO、PlanExecutor、背包整理、箱子自動化、裝備耐久監控
- [x] 復活後恢復任務、self_task workflow mode、Spatial memory 接入 self_task
