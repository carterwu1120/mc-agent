# Minecraft Bot — Roadmap

## 進行中 / 近期

- [ ] **修正 executor 接受 activity_stuck replan 時，舊步驟沒有被完整覆蓋**
  - 目前 `activity_stuck` 已能產生較完整的 deterministic replan，但從 log 來看，
    executor 在接受 replan 後，仍可能繼續執行舊的 pending steps，造成
    `hunt/getfood/equip/mine diamond` 與新 replan 混在一起跑。
  - 症狀：
    - `hunting/no_animals` 後明明回了 `explore -> hunt -> getfood ...`
      ，但 executor 仍直接往後執行舊的 `getfood`、`equip`、甚至 `mine diamond`
    - 最後看起來像「附近沒食物就直接去挖鑽石」，其實是 replan 套用不完整
  - 解法：
    - `executor.replan(...)` 必須原子地覆蓋剩餘步驟
    - replan 後不能再漏跑舊的 step index
    - `equip` / `mine diamond` 不應在 `hunt/getfood` 未完成時提前執行

- [ ] **Post-action verification loop**
  - 目前 executor 送出指令後，收到 `action_done` 就直接進下一步，完全不驗證動作是否真的成功。
    例如 `equip` 完裝備狀態沒變、`smelt iron 3` 但 iron_ingot 只多了 1 個、
    `mine diamond 10` 但實際只挖到 7 個——executor 都不知道。
  - 解法：在關鍵指令（equip / smelt / mine）完成後，比對 before/after state，
    若有落差則當作 stuck 處理（附具體原因），而非假設成功。
  - 對 game bot 尤其重要，因為 Minecraft 世界隨時會變（掉落物消失、被打死、方塊被別人挖）。

- [ ] **Deterministic rules 下沉到系統層（不只靠 prompt）**
  - 目前很多「每次都必須成立」的規則只存在 system prompt 裡，LLM 偶爾會忘記或在長 context 下失效。
    例如「replan 必須附加 pending_steps」、「smelting stuck 且上層是 mining 不能直接 skip」。
  - 解法：在 Python 層加硬性驗證函數，LLM 回傳後立即檢查，不符合就 reprompt 或
    fallback，而非期待 prompt 每次都引導正確。
  - 原則：Prompt 描述意圖，系統層保證規則一定執行。

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
  - [ ] **Task memory 補強**（task.json / task history）
    - 目前已經有 task.json、steps、currentStep、interrupt / done / resume、step context 與 resumetask，
      但還缺更完整的歷史與回顧能力。目標是讓 bot 在重啟、compact、replan 多次之後，
      仍能知道「之前做到哪、為什麼停下來、這次變更過哪些步驟」。
    - [x] 保留當前 task、steps、currentStep、step status、step context
    - [x] 支援 interrupted / resumed / done 的基本任務生命週期
    - [x] planner / executor / self_task 可讀取目前 task 脈絡
    - [ ] 記錄完整 task history（不只覆蓋目前 task.json）
    - [ ] 記錄 recent replans、skip、abort、resumetask 的歷史原因與時間線
    - [ ] 讓 planner / self_task 可讀取「最近失敗模式」而不只讀當前 task 狀態
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

- [ ] **強化 self_task 自主規劃**（依賴 Spatial memory 完成後）
  - [ ] 目標分解：收到「做鑽石套裝」類指令時，自動推算缺多少鑽石 → 規劃採礦鏈
  - [ ] 資源導向規劃：缺某樣資源時，優先去 spatial memory 裡的已知位置，而非重新探索

- [ ] **Python 側 context 清理機制**
  - 長時間運作後，每次 LLM call 會夾帶大量舊 state，導致 context 品質劣化、成本上升。
    需要定期清理 / 壓縮不再需要的歷史事件，讓主線 context 保持精簡。
    （概念類似 Claude Code 的 subagent context firewall：只把結論傳回主線，不讓過程污染主 context。）

- [ ] **Dashboard**（單 bot 即時監控）
  - Python 側加輕量 HTTP server，expose `/state` endpoint；
    簡單 HTML 頁面顯示：activity / progress / 背包 / 裝備 / 最近 LLM 決策。

- [ ] **Docker 化單 bot**（確認 container 能正常啟動）

## Multi-Agent

- [ ] **設計 Coordinator agent**（任務分配、跨 bot 狀態共享）
  - 考慮用 LangGraph 做 Coordinator 的決策圖（哪個 bot 去挖礦、哪個砍樹）；
    個別 bot 的內部邏輯仍保持現有架構。
- [ ] 加入 bot 間溝通機制（遊戲內 chat + Python message queue）
- [ ] k8s 部署 n 個 bot container

## 已完成

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
