# Minecraft Bot — Roadmap

> 標記 **[Backend]** 的項目對應真實的 backend / system design 概念。

## 進行中 / 近期

- [ ] **Manual override / interrupt 機制**
  - [ ] 自然語言 interrupt / resume 分類（不只靠前綴）
  - [ ] executor / stuck recovery 能接受人工覆蓋，避免舊流程在背景等待

- [ ] **Python 側 context 清理機制 v2**
  - [ ] v2：activity_stuck / verify_failure / 其他 skill 也統一接到共用 context builder
  - [ ] v2：加入重複事件折疊、按 skill 類型設定 context budget

- [x] **Rate Limiting（LLM 請求流量控制）** `[Backend: API stability / token bucket]`
  - Token bucket + exponential backoff 已實作在 `agent/brain/rate_limiter.py`

- [ ] **通用 tool acquisition policy**
  - 目標：`mining` / `woodcutting` / `combat` 不各自實作 `ensurePickaxe` / `ensureAxe` / `ensureSword`
  - [ ] 共享 retry cooldown：上次 craft 失敗的 resource fingerprint，inventory 有哪些變化才允許重試
  - [ ] 統一 fallback：可徒手繼續的先繼續；不可徒手才升級成 replan

---

## 中期

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

---

## 已完成

- [x] **Structured Logging + Observability** `[Backend: Observability]`
  - JS bot log 改成 JSONL（`{"time", "level", "service", "bot_id", "task_id", "msg"}`）
  - executor 每條指令帶 `_task_id`，JS logger 同步更新，bot/brain log 可用 task_id 串接
  - LLM call latency 記錄（`[LLM] ok latency=Xs`）
  - `GET /metrics?hours=N`：task 成功率、stuck by reason/activity
  - log 檔自動輪替（7 天後刪除）

- [x] **Multi-agent routing 收尾**
  - 啟動 log 印出 `MC_USERNAME` / `BOT_USERNAMES` / `STRICT_CHAT_ADDRESSING`
  - 統一 system chat / server announcement 過濾，避免 `Teleported ...` 被送進 planner

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

- [x] **Vertex AI provider（VertexClient）**
  - ADC 認證、`LLM_PROVIDER=vertex`

- [x] **Dashboard**（agent observability）
  - `agent/dashboard.py` + `agent/dashboard.html`、SQLite history endpoint

- [x] **Goal-level verification（任務目標驗證）**
  - `_verify_goal()`：plan 全部 steps 跑完後，用 plan 開始/結束 inventory snapshot 驗收最後一個 output 指令
  - 涵蓋：mine / smelt / chop / fish / hunt（嚴格比對目標數量，非只 > 0）
  - `_build_goal_remediation()`：動態計算 deficit，能補的就補（smelt 不夠 → 先 mine 再 smelt）；只重試一次（`_goal_retry` 參數）
  - `task_memory.done(goal_verified=bool)`：記錄 `goalVerified` 欄位
  - 剩餘（低優先）：status `done` 改為 `completed` / `finished` 字串區分

- [x] **Post-action verification loop**
  - `_verify_step()`：equip / smelt / mine / deposit / fish / hunt 完成後比對 before/after state
  - 驗證失敗 → LLM 決策（replan / skip / accept）

- [x] **Stability fixes（overnight run 診斷）**
  - cobblestone 改直挖（`_digStraightDown`），不走 tunnel 邏輯避免山地卡死
  - `_digTunnel` 加 `noProgressSteps` 計數器，連續 3 步無前進即放棄
  - inventory_full 與 activity 完成的 race condition（`waitUntilIdle` 防止 smelt 指令被丟棄）
  - executor 加 90s idle 偵測：activity 指令送出後若 JS 仍未啟動，自動重送一次
  - Dockerfile.agent 補 `2>>` stderr redirect，Python crash 可查 `agent/logs/stderr-*.log`

- [x] **Deterministic rules 下沉到系統層**
  - `_enforce_pending_steps`、`_filter_done_steps_from_replan`、`_deduplicate_adjacent_cmds`、`_block_invalid_skip`

- [x] **context_builder v1**、**Task memory 補強**、**SQLite task history**
- [x] **activity_stuck.py 重構成 `skills/stuck/` 分目錄**
- [x] Activity stack LIFO、PlanExecutor、背包整理、箱子自動化、裝備耐久監控
- [x] 復活後恢復任務、self_task workflow mode、Spatial memory 接入 self_task
