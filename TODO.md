# Minecraft Bot — Roadmap

## 進行中 / 近期

- [ ] 強化 planner 前置條件推理（模糊指令 → 自動展開完整 plan）
- [ ] 強化 self_task 自主規劃能力（探索、找資源、目標分解）
- [ ] executor 錯誤處理（step 失敗時不卡住，能 skip 或 abort）
- [ ] 復活後要檢查task.json裡面的steps，看哪一個是剛剛還在執行的，再判斷要不要繼續執行。而不是直接蓋過
## 中期

- [ ] 加入 per-agent 中期記憶（探索記錄、資源地圖，存 DB）
- [ ] Docker 化單 bot（確認 container 能正常啟動）

## Multi-Agent

- [ ] 設計 Coordinator agent（任務分配、跨 bot 狀態共享）
- [ ] 加入 bot 間溝通機制（遊戲內 chat + Python message queue）
- [ ] k8s 部署 n 個 bot container

## 已完成

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
