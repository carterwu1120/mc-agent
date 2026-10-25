# 卡住恢復策略

## 原則

- 舊活動已中斷，不會自動恢復。保留原任務目標與剩餘步驟，數量扣除已取得的部分。
- 依 reason、detail、progress、navigation（挖礦另看 mining_result）與目前狀態選策略。策略必須改變某些條件（位置、工具、材料或做法）；不可重送已失敗的策略，也不可跳過必要前置條件假裝完成。
- 腳本做不到的事要坦白說明，並請玩家協助。
- 天色變化不是放棄或回基地的理由；只有明確危險證據（血量或飢餓危險、敵對生物、岩漿或水）才以安全為主要理由。

## watchdog 判定（任何活動）

- `no_progress`：25 秒（釣魚 60 秒）沒有新位置或新產出 → 換地方（surface、explore、back）再重新開始。
- `no_task_progress`：移動 180 秒卻沒有產出目標資源 → 區域不對，explore、換深度或換資源來源。
- `wait_expired`：有期限的等待（熔爐、魚上鉤、背包）逾時 → 檢查前提條件：燃料、水、箱子空間。
- `orphaned_pause`：活動暫停 45 秒沒有恢復 → 以剩餘數量重新開始。

## 挖礦

- `no_tools`：從最便宜的缺口補工具鏈，再以剩餘數量接回 mine。
- `no_blocks`：先 surface 再 explore surface，或換深度；鑽石在 y ≈ −58。
- `no_safe_route`：岩漿、水或落差擋路 → home 或 surface 後從別處重試。
- `water_loop` / `trapped_in_water`：先 home 撤離，再接回剩餘步驟。
- 四面都是基岩或不可挖的方塊：請玩家用 /tp 救援。

## 砍樹

- `no_trees`：在地底先 surface；已在地表就 explore trees，再 chop 剩餘數量。不要原地重送 chop。

## 回到地表

- surface 失敗：改用 back，或在已設基地時用 home；不要再 surface。

## 釣魚

- `no_rod`：bot 不會做釣竿，改用 hunt，或先取得釣竿。
- `bad_cast` / `no_progress`：換到其他水邊，或 explore 到別的水域。

## 冶煉

- `no_fuel`：接下來要挖礦就先略過（挖礦會得到煤）；否則先 chop 取得木頭再 smelt。
- `no_input`：先取得原料。
- `missing_dependency`：缺木材 → chop；缺圓石（熔爐要 8 個）→ mine stone。
- 熔爐放不下或沒有進展：若冶煉是上層挖礦的必要前置，換到開闊位置重試，不能跳過；若冶煉本身是主任務，重試一次仍失敗就說明原因。

## 狩獵與補食物

- `no_animals`：附近沒有可食用動物，這次狩獵不算完成。生食已夠先處理（≥ 8，或 ≥ 剩餘需求的一半）→ 先 getfood；有釣竿且在水邊 → fish 再 getfood；否則 explore animals，再 hunt 剩餘數量。
- `no_weapon`：沒有武器且本地合成已失敗，不要原地追動物。熟食已夠就停止補食；有釣竿就改 fish；缺木材但附近有樹 → chop logs 2 再 hunt。都不可行時說明並等待。
- `no_raw_food`（getfood）：先 hunt 或 fish 取得生食，再 getfood count <還需熟食數量>（不是原本的總目標）。
- `cook_failed`（getfood）：烹飪沒有產出熟食，依冶煉的原因處理（燃料、熔爐位置）後再 getfood。

## 箱子

- makechest 失敗：有空位的已登記箱子就直接 deposit；木材夠（等效 16 木板）就重試；缺木材先 chop；都不行就告知玩家。

## 工具準備

- `tool_preparation_failed`：補足缺少的材料（木板與木棍；石鎬 3 圓石；鐵鎬 3 鐵錠；鑽石鎬 3 鑽石）再重試。
