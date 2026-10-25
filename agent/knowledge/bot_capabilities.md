# Bot 能力與限制

## 鎬子與工具鏈

- `mine <ore> <n>`（含 `mine stone`）開始時若沒有足夠等級的鎬子，直接回報 `no_tools`。途中鎬子壞掉時，會用背包現有材料補一把同等級的；材料不夠才回報 `no_tools`。
- 最低等級：iron、lapis、copper → stone_pickaxe；gold、redstone、diamond、emerald → iron_pickaxe；ancient_debris → diamond_pickaxe；其他 → wooden_pickaxe。
- 只補缺的那一段：木鎬 `chop logs 3` → `preparetool wooden_pickaxe`；石鎬 `mine stone 3` → `preparetool stone_pickaxe`；鐵鎬 `mine iron 3` → `smelt raw_iron 3` → `preparetool iron_pickaxe`；鑽石鎬 `mine diamond 3` → `preparetool diamond_pickaxe`。

## 裝備（equip）

- 盔甲沒有合成指令，只會在戰鬥結束後或盔甲耐久 ≤ 10% 時，於閒置狀態用背包材料自動補製（鑽石優先，否則鐵錠）。
- 只在玩家要求、剛取得新武器或護甲，或要 hunt / combat 且背包有更好的裝備時才加 `equip`。mine / chop / fish / smelt 會自己換工具，前面不要加 `equip`。
- combat 不是準備動作。

## 食物

- 飢餓 < 18 且有食物時會自動進食。
- `getfood` 會用熔爐把背包裡的生食烤熟，但不會取得生食；`fish` 需要背包已有釣竿（不會自動製作）且在水邊。hunt / fish / getfood 都要帶明確正整數。
- 數量 = 任務需要的熟食 − 現有熟食。生食不夠且附近有動物才 hunt；有釣竿且附近有水才 fish。沒有本地來源時請玩家補給或帶路，不主動跑圖。不要假設每隻動物掉兩個生食，也不要囤積。
- 飢餓接近 20 且任務短、風險低時，不需要補食物。

## 食物不足時的數量

食物不足事件（飢餓 < 10 且沒有食物）的目標 = max(1, ceil((20 − 飢餓) / 4) + 風險緩衝 − 現有熟食)。每份熟食約回 4 點飢餓；目前或下一個活動是 mining、combat、hunting、fishing 時，風險緩衝為 1。

## 冶煉與燃料

- `smelt` 只處理背包已有的原料，需要熔爐（8 個圓石）與燃料。
- 燃料優先用木頭或木板（1 個原木約燒 1.5 個）；背包完全沒有木頭也沒有煤時，才 `mine coal`。

## 木材與箱子

- `makechest` 需要等效 16 木板（原木 × 4 + 木板，或 2 個 chest）。
- 已有對應類別且有空位的箱子就直接 `deposit <id>`。

## 移動與位置

- 不提供探索指令或持久化資源位置記憶。只依當下 nearby/entities 等觀察採集，不可自行猜測資源座標或以隨機 tp 取代探索。缺少本地來源時說明阻礙並等待玩家補給或指定目的地。
- 保留走向玩家、指定目的地、回家、返回與上地表，以及挖礦內部下挖／挖隧道；這些不是自主跑圖。

- `home`、`back`、`tp` 透過伺服器 `/tp`，需要權限。
- 沒有基地不能用 `home`，要先 `sethome`。

## 背包

- 背包達 34/36 格時活動暫停，bot 會先自動壓縮、丟垃圾裝備、埋雜物，空間仍不足才需要決策；這時採集類活動不會開始。
