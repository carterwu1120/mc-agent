# Minecraft Bot — Roadmap

## 進行中 / 近期

- [ ] **Task memory 補強**（agent working memory）
  - 目標：補齊短期任務記憶，讓 bot 在重啟、compact、replan 多次之後，仍能知道「之前做到哪、為什麼停下來、這次變更過哪些步驟」。
  - [ ] 若之後需要完整歷史，再另外補 `task_history.jsonl` 或 SQLite；`task.json` 仍維持短期工作記憶

- [ ] **Python 側 context 清理機制**
  - 長時間運作後，每次 LLM call 會夾帶大量舊 state，導致 context 品質劣化、成本上升。
    需要定期清理 / 壓縮不再需要的歷史事件，讓主線 context 保持精簡。
    （概念類似 Claude Code 的 subagent context firewall：只把結論傳回主線，不讓過程污染主 context。）
  - 依賴：優先利用 task / interaction / reflection memory 的摘要，而不是直接把原始歷史塞進 prompt

- [ ] **Dashboard**（agent observability）
  - Python 側加輕量 HTTP server，expose `/state` endpoint；
    簡單 HTML 頁面顯示：activity / progress / 背包 / 裝備 / 最近 LLM 決策。
  - 建議資料模型從一開始就保留 multi-agent 擴充空間，例如 `agents[]` 而不是只綁單一 bot

- [ ] **Manual override / interrupt 機制**
  - 目標：當 bot 正在執行 task、等待 stuck recovery、或卡在某個 activity 時，玩家可以可靠地打斷它，讓它優先執行新的要求
  - 需要同時支援兩種入口：
    - 顯式指令：例如 `!interrupt chop logs 4`、`!abort`、`!resume`
    - 自然語言：例如「你先去砍樹」、「先別挖了，回地表」、「先來找我」、「先停一下」
  - 系統層需要明確區分：新任務 / 插隊任務 / 取消當前任務 / 恢復原任務，而不是只把這些話當成普通 chat 丟給 planner
  - executor / activity_stack / stuck recovery 需要能接受人工覆蓋訊號，避免舊流程仍在背景等待，和新任務互相打架
  - 下一步聚焦：補自然語言 interrupt / resume 分類，不只靠前綴判斷
  - 後續可延伸成 dashboard 上的手動控制入口，並作為 multi-agent coordinator 介入單一 bot 的基礎能力

- [ ] **強化 self_task 自主規劃**（Spatial memory 已接入，下一步做 deterministic 強化）
  - [x] 已把 exploration memory 接入 self_task prompt，LLM 已能看到已知礦點 / 森林 / 動物區
  - [ ] 目標分解下沉：目前 planner 已支援部分裝備 / 工具需求的缺口推算；下一步是把這種推理擴展成更通用的系統層目標分解，而不只限於 equipment 類需求
  - [ ] 資源導向規劃下沉：缺某樣資源時，優先查詢 spatial memory 的已知位置，再決定是否 explore
  - [ ] 補 deterministic 選點策略：多個已知資源點時，定義最新 / 最近 / 最可信的選擇規則，而不是完全交給 LLM 自由發揮

- [ ] **Next: 通用 tool acquisition policy（工具取得/重試策略下沉）**
  - 目標：不要讓 `woodcutting` / `mining` / `combat` 各自發明一套 `ensureAxe` / `ensurePickaxe` / `ensureSword` 重試流程
  - 抽出共享策略：最近一次工具合成是否失敗、當時資源摘要、什麼條件變化後才允許再試
  - 統一 fallback 規則：可徒手繼續的活動先繼續；不可徒手的活動才升級成 recovery / replan
  - 預期收益：減少「局部跳針」、降低 activity_stuck 噪音、讓後續新增技能時不用重複補 debounce 邏輯

## 中期

- [ ] **Memory roadmap**
  - 目標不是只做 spatial memory，而是逐步建立完整的 agent memory system，讓 bot 不只記得資源位置，也能延續任務、互動與建議。
  - [ ] **Spatial memory**（exploration_memory.json）
    - Bot 目前對世界的認識在重啟後歸零。需要把「去過哪、在哪找到什麼」寫到外部檔案，
      讓 self_task 下次能優先去已知有礦/有樹/有動物的位置，而非每次隨機 explore。
    - [x] JS 側：挖到礦 / 看到動物 / 砍到樹時附位置資訊到 activity_done
    - [x] Python 側：記錄 ore_finds / forest_finds / animal_areas 到 exploration_memory.json
    - [x] self_task 讀取記憶，優先去已知資源位置規劃任務
    - [ ] 補上 biome / explored_chunks / 已探索區域密度，讓 explore 類任務不只記資源點，也記地圖覆蓋狀態
    - [ ] 記錄已知工作點（礦坑入口 / 熔爐 / 工作檯 / 常用補給點），讓 self_task 能規劃更穩定的往返路線
  - [ ] **Task history / long-term task recall**
    - 在 working memory 之外，另外補完整 task history（例如 `task_history.jsonl` / SQLite），支援時間線回顧與長期分析。
    - [ ] 記錄完整 task history（不只覆蓋目前 task.json）
    - [ ] 規劃與 `Dashboard` / multi-agent coordinator 的歷史查詢接口
  - [ ] **Interaction memory**（interaction_memory.json）
    - 讓 bot 不只記得任務，也記得玩家偏好、近期重要對話、長期目標與尚未結束的主題，
      讓互動更像持續合作，而不是每次都從零開始。
    - [ ] 保存玩家偏好（例如少問問題、偏好 deterministic 行為）
    - [ ] 保存長期目標與最近重要對話摘要
    - [ ] 保存 open threads（聊到一半但還沒完成的主題）
  - [ ] **Reflection / suggestion memory**（reflection_memory.json）
    - 讓 bot 能累積對世界與自己行為的觀察，例如哪裡常卡住、哪些資源策略有效、最近值得提醒玩家的事。
    - [ ] 記錄常見 failure patterns 與改善建議
    - [ ] 記錄已知工作點、常用礦坑、危險區域、可重用設施
    - [ ] 支援 bot 主動在視窗分享觀察、建議與下一步提醒

- [ ] **通用 craft / ensure retry 記憶體**
  - 目前 `woodcutting` 的 `ensureAxe` 已看出局部跳針：同一輪內重複補工具、材料剛變一點就整套重試
  - 不先做每個 skill 的局部修補；之後應抽成共享機制，讓所有 `ensureTool` 類流程共用
  - 方向：記錄最近一次 craft/ensure 嘗試的資源 fingerprint、成功/失敗結果、cooldown、以及「inventory 哪些變化才值得重試」
  - 先列為架構型技術債，避免現在為 `woodcutting` 單點加太多特例

- [ ] **Docker 化單 bot**（確認 container 能正常啟動）

## Multi-Agent

- [ ] **設計 Coordinator agent**（任務分配、跨 bot 狀態共享）
  - 考慮用 LangGraph 做 Coordinator 的決策圖（哪個 bot 去挖礦、哪個砍樹）；
    個別 bot 的內部邏輯仍保持現有架構。
- [ ] 加入 bot 間溝通機制（遊戲內 chat + Python message queue）
- [ ] k8s 部署 n 個 bot container

## 已完成

- [x] **修正 executor 接受 activity_stuck replan 時，舊步驟沒有被完整覆蓋**
  - 根本原因：LLM replan 沒有附上 pending_steps，導致舊步驟被丟棄
  - 修正：`_enforce_pending_steps` 自動補上漏掉的 pending_steps
  - 修正：`plan_context.pending_steps` 改用 `steps[idx+1:]` slice，包含 failed steps
  - 修正：executor replan 分支加強 log 確認替換是否正確

- [x] **Post-action verification loop**（強制 LLM 介入已實作）
  - `executor.py` 實作 `_verify_step()`，在 equip/smelt/mine/deposit 完成後比對 before/after state
  - `_handle_verify_failure()`：驗證失敗時重新進入 stuck recovery，觸發 LLM 決策（replan / skip / accept）
  - `agent.py` 設定 `executor._verify_failed_callback = _on_verify_failed`，路由到 `activity_stuck_skill`
  - LLM 不回 replan/skip 時自動 resume（避免 executor 永遠等待）

- [x] **Deterministic rules 下沉到系統層（不只靠 prompt）**
  - `activity_stuck.py` 實作完整 pipeline：
    - `_enforce_pending_steps` — replan 缺少 pending_steps 時自動補上
    - `_filter_done_steps_from_replan` — 移除 replan 開頭重複的已完成步驟
    - `_deduplicate_adjacent_cmds` — 移除連續重複指令
    - `_block_invalid_skip` — 由 `_CRITICAL_DEPENDENCY_PAIRS` table 驅動，攔截非法 skip
    - `_compute_is_critical_subtask` — Pre-LLM 注入 `is_critical_subtask` 到 prompt

- [x] **Planner task context 強化**
  - `task_memory.load_any()` — 不過濾 status，讓 planner 看到所有任務（running / interrupted / done）
  - `task_memory.save()` 加 `final_goal` 欄位，自動繼承前次任務的最終目標
  - `task_memory.set_final_goal()` — LLM 可更新跨任務的最終目標
  - Planner prompt 依 status 顯示不同標籤，並帶入 `final_goal` 上下文
  - LLM 可在 plan response 輸出 `final_goal`，agent.py 自動儲存

- [x] **修正 equip 指令 action_done 遺漏**
  - `equip <specific_item>` 在物品不在背包時，原本不送 `action_done` → executor 永遠等待
  - 修正：無論是否裝備成功，always 送 `action_done`

- [x] **修正 planner 對 equip 的濫用**
  - 採礦/砍樹前不再盲目加 equip（mining.js 自動換鎬）
  - Planner prompt 明確規範 equip 使用時機：玩家明確要求、剛合成新裝備、或有更好武器/護甲需穿上

- [x] **新增 `!test verify_failure` 情境 + `test_plan` bridge event**
  - `test_plan` 事件讓測試直接注入 commands 給 executor，繞過 planner
  - `verify_failure` 情境：清空背包 → 注入 `equip diamond_pickaxe` → 驗證失敗 → LLM 介入

- [x] **修正 mining 無鎬時 push/pop smelting tight loop**
  - 症狀：`[Mine] 無稿子 → [Craft] 有 raw_iron → push smelting → 找不到熔爐 → pop → resume → 無限重複`
  - 根本原因：`_smeltIfNeeded` 有圓石就直接啟動 smelting，但沒有木材做工作台，熔爐無法放置，瞬間失敗又回到 mining
  - 修正（`crafting.js`）：無現成熔爐時，提前檢查「有工作台（附近 4 格或背包）OR 有木材」，否則直接 return false 不啟動 smelting
  - 修正（`crafting.js`）：smelting 結束後檢查 `consumeLastOutcome`，若 status=stuck 不回報成功
  - 修正（`smelting.js`）：`找不到熔爐` 時補送 `bridge.sendState(activity_stuck)`，讓 Python 有機會規劃補木材等 recovery

- [x] `activity_stuck.py` 重構成 `skills/stuck/` 分目錄
  - 已拆出 `smelting.py`、`mining.py`、`hunting.py`、`getfood.py`
  - 通用 decision 驗證拆到 `skills/stuck/decision.py`
  - LLM JSON 修補 / reprompt 拆到 `skills/stuck/llm_utils.py`
  - prompt 常數拆到 `skills/stuck/prompts.py`
  - prompt 組裝拆到 `skills/stuck/prompt_builder.py`
  - 主 `activity_stuck.py` 現在以派發與 orchestration 為主
- [x] Activity stack LIFO 架構
- [x] PlanExecutor + task_memory
- [x] 背包整理（inventory_full LLM 決策）
- [x] 箱子自動化（makechest + labelchest + deposit，含 {new_chest_id} placeholder）
- [x] 裝備耐久監控（durability_pct，equipment slots）
- [x] 背包滿時攔截所有活動（commands.js checkFull）
- [x] 忽略 / 開頭的 Minecraft 指令
- [x] Planner 禁止 LLM 使用清單外指令
- [x] CLAUDE.md 分層（agent / bot / skills）
- [x] Planner prompt 加入箱子詳情（label, freeSlots, contents）
- [x] 復活後檢查 task.json steps，判斷是否繼續執行
- [x] self_task 所有模式優先恢復中斷任務（resume_task flag）
- [x] activity_stuck 加上層活動堆疊 context（smelting 不再誤 skip）
- [x] activity_stack 顯示修正（entry.get("activity") 而非 "name"）
- [x] 工作檯 / 熔爐放置改為前方兩格，被擋則挖除
- [x] blockUpdate timeout 不再誤判為放置失敗（確認方塊存在再判斷）
- [x] 大箱子放置驗證同 Y + XZ 距離 = 1（避免斜對角）
- [x] tp 指令加入 planner（恢復任務時可傳送到上次工作位置）
- [x] 強化 planner 前置條件推理（模糊指令 → 自動展開完整 plan）
- [x] executor 錯誤處理（step 失敗時不卡住，能 skip 或 abort）
- [x] **Task memory 基礎版**
  - 保留當前 task、steps、currentStep、step status、step context
  - 支援 interrupted / resumed / done 的基本任務生命週期
  - planner / executor / self_task 可讀取目前 task 脈絡
  - `task.json` 整合短期 interrupted memory（`interruptedTasks`）與 recent transitions（`recentTransitions`）
  - 寫入 `task.json` 時自動做 normalize / prune，維持短期工作記憶而不是無限累積
- [x] **Task memory 事件 / 失敗模式補強**
  - 記錄 recent replans、skip、abort、resumetask 的歷史原因與時間線
  - planner / self_task 可讀取 `recentFailures`，不只讀當前 task 狀態
- [x] **恢復挖礦 / 食物鏈 deterministic 補強**
  - `回去 / 恢復 / 繼續挖礦` 類語句優先走 deterministic 恢復最近 mining task，而不是完全交給 LLM 重規劃
  - 恢復 mining / diamond 任務時，若熟食已足夠（目前先用 `cooked_total >= 16`），不再重建 `hunt / getfood` 前置鏈
  - `hunting -> mine` 的 no-weapon shortcut 改成真正終止 hunting activity，而不是把 hunting 殘留在 stack 裡
