# 背包整理

背包達 34/36 格時要提前整理，保留合成空間。bot 詢問前已自動壓縮資源方塊、丟垃圾盔甲、埋已知雜物。

## 處理方式

- 丟棄：列出英文 item name，該物品會整組全部埋掉，不能指定數量。
- 存箱子：停止活動 → 存放 → 回原位 → 以剩餘數量（原目標 − 已完成）重啟。
- 繼續：沒有可丟的、也不適合存箱子時才選。活動是 smelting 時不能選繼續（取不出成品會永久卡住），要丟棄或存箱子。

## 存箱子的路徑（依序判斷）

1. 有已分類且有空位的箱子，且已設基地：停止 → `home` → `deposit <id>` → `back` → 重啟
2. 沒有可用箱子，木材夠做箱子（原木 × 4 + 木板 ≥ 16），且已設基地：停止 → `home` → `makechest` → `labelchest {new_chest_id} <label>` → `deposit {new_chest_id}` → `back` → 重啟
3. 沒有基地但木材夠：同路徑 2，但把 `home` 換成 `surface` → `sethome`
- 沒有基地且木材不夠：不能用 `home`，也不做箱子，只能丟棄或繼續。
- label 依最多的材料類型：wood / ore / stone / misc。
- 停止 / 重啟：mining `stopmine` / `mine <ore> <n>`；chopping `stopchop` / `chop logs <n>`；fishing `stopfish` / `fish catches <n>`；smelting `stopsmelt` / `smelt <material> <n>`；idle 不需要。

## 不能丟

- 食物；鐵製以上的工具與武器；礦石、原礦、coal、diamond、emerald、lapis_lazuli；原木與木板；string；其他稀有或有合成價值的物品。
- 裝備欄的物品不佔背包格，不能列入丟棄。

## 可以丟

- 木製、石製工具；多餘的 crafting_table（留 1 個）
- bone、rotten_flesh、spider_eye、clay_ball、clay、raw_copper、copper_ingot、各種樹苗、lily_pad、tripwire_hook
- 超過 16 支的 arrow
- 耐久 ≤ 10% 的工具或裝備（沒有同類備品、且裝備欄同槽位也壞了時保留）

## 依活動

- 挖礦：cobblestone / cobbled_deepslate 留 64 個（封洞用），其餘丟；diorite、andesite、granite、tuff、gravel、flint、pointed_dripstone、dirt、sand 全丟。
- 其他活動：cobblestone / cobbled_deepslate 留 128 個；diorite、andesite、granite、tuff 超過 32 個才丟；gravel、flint、pointed_dripstone、dirt 全丟。
