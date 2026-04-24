# Minecraft Bot — Roadmap

> 標記 **[Backend]** 的項目對應真實的 backend / system design 概念。

## 進行中 / 近期

- [x] **Multi-agent routing 收尾**
  - [x] 啟動 log 印出 `MC_USERNAME` / `BOT_USERNAMES` / `STRICT_CHAT_ADDRESSING`，方便確認生效設定
  - [x] 統一 system chat / server announcement 過濾，避免 `Teleported ...` 被送進 planner

- [ ] **Manual override / interrupt 機制**
  - [ ] 自然語言 interrupt / resume 分類（不只靠前綴）
  - [ ] executor / stuck recovery 能接受人工覆蓋，避免舊流程在背景等待

- [ ] **Python 側 context 清理機制 v2**
  - [ ] v2：activity_stuck / verify_failure / 其他 skill 也統一接到共用 context builder
  - [ ] v2：加入重複事件折疊、按 skill 類型設定 context budget

- [ ] **Structured Logging** `[Backend: Observability]`
  - 為什麼需要：現在 log 是純文字，跨 service 難以追蹤問題根源。加上 `task_id` 後，
    一個任務從 coordinator 派出 → agent 接收 → bot 執行的整條流程都能串起來查。
  - [ ] log 改成 JSON 格式（`{"time", "level", "task_id", "service", "msg"}`）
  - [ ] coordinator / agent / executor 的操作都帶同一個 `task_id`
  - [ ] dashboard `/events?task_id=xxx` 可查整條 trace

- [ ] **Goal-level verification（任務目標驗證）** `[從中期移入]`
  - 目標：plan 全部 steps 跑完後，驗證是否真的達成 goal，而不只是 steps 跑完就算 done
  - 現有的 `_verify_step` 是 step-level（equip/smelt/mine 個別確認），缺 goal-level
  - [ ] `PlanExecutor` plan 完成後，拿 `goal` + before/after inventory snapshot 做 goal 驗收
  - [ ] 驗收失敗 → replan（優先 deterministic，不一定需要 LLM）
  - [ ] `task_memory` status `done` 改為區分 `completed`（goal 達成）vs `finished`（steps 跑完）

- [ ] **Rate Limiting（LLM 請求流量控制）** `[Backend: API stability / token bucket]` `[從中期移入]`
  - 為什麼需要：多個 bot 同時卡住時，可能在短時間打出大量 LLM request 超過 API quota。
    Token bucket 讓系統在壓力下仍然穩定，超過限制時 queue 等待而不是直接報錯。
  - [ ] LLM client 層加 token bucket rate limiter（每分鐘最多 N 次）
  - [ ] 超過限制時 exponential backoff 等待，不拋例外

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

- [x] **Post-action verification loop**
  - `_verify_step()`：equip/smelt/mine/deposit 完成後比對 before/after state
  - 驗證失敗 → LLM 決策（replan / skip / accept）

- [x] **Deterministic rules 下沉到系統層**
  - `_enforce_pending_steps`、`_filter_done_steps_from_replan`、`_deduplicate_adjacent_cmds`、`_block_invalid_skip`

- [x] **context_builder v1**、**Task memory 補強**、**SQLite task history**
- [x] **activity_stuck.py 重構成 `skills/stuck/` 分目錄**
- [x] Activity stack LIFO、PlanExecutor、背包整理、箱子自動化、裝備耐久監控
- [x] 復活後恢復任務、self_task workflow mode、Spatial memory 接入 self_task
