import json
import re
from agent.brain import LLMClient
from agent.skills.state_summary import equipment_summary

SYSTEM_PROMPT = """你是 Minecraft 機器人的背包管理助手。
背包格數已達警戒線（34/36 格），需要提前整理以保留合成空間。
策略：提前在背包快滿時就處理，而不是等到完全滿再亂丟，目的是確保合成物品時背包有足夠空間。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"action": "drop", "items": ["diorite", "tuff"]}
{"action": "plan", "commands": ["stop指令", "home", "deposit 1", "back", "resume指令"]}
{"action": "continue"}

選 plan 的時機與格式：

① 有已分類箱子且有空間（freeSlots > 0）：
{"action": "plan", "commands": ["stop指令", "home", "deposit <id>", "back", "resume指令"]}
commands 裡的 chest id 從下方提供的箱子資訊取得。

② 沒有已分類箱子，但背包有足夠木材可做箱子（wood_as_planks ≥ 16，logs 或 planks 皆可）：
{"action": "plan", "commands": ["stop指令", "home", "makechest", "labelchest {new_chest_id} <label>", "deposit {new_chest_id}", "back", "resume指令"]}
{new_chest_id} 是佔位符，makechest 完成後會自動填入實際 id，不要替換成數字。
label 根據背包最多的材料類型選擇：wood / ore / stone / misc。

resume 指令的數量填入剩餘目標（原目標 - 已完成數量）。

【各活動對應的 stop / resume 指令格式】
- mining   → stop: stopmine  / resume: mine <ore> <count>     例：mine diamond 41
- chopping → stop: stopchop  / resume: chop logs <count>      例：chop logs 20
- fishing  → stop: stopfish  / resume: fish catches <count>   例：fish catches 30
- smelting → stop: stopsmelt / resume: smelt <material>       例：smelt iron
- idle     → 不需要 stop，resume 留空（只執行 home deposit back）

【裝備欄狀態說明】
- 裝備欄（主手、頭盔、胸甲、護腿、靴子）不佔背包格，無法用 drop 丟棄
- 若裝備耐久 0%（已損壞），代表該裝備實際上已無法使用，背包若有備用則可考慮用 equip 換上
- 裝備欄資訊僅供判斷，不能列入 drop 的 items 清單

【絕對不能丟的物品】
- 食物（cooked_beef、bread、fish 等）
- 鐵質以上工具與武器（iron_pickaxe、iron_axe、sword 等）
- 礦石與原礦（ore、raw_iron、raw_gold、raw_copper、lapis_lazuli、coal、diamond、emerald 等）
- 木材與木板（oak_log、planks 等，合成用）
- string（製作弓、釣竿必要材料）
- 任何稀有或有合成價值的物品

【可以丟的低成本工具（重新製作不耗費珍貴資源）】
- wooden_pickaxe、wooden_axe、wooden_shovel 等木製工具
- stone_pickaxe、stone_axe、stone_shovel 等石製工具
- 多餘的 crafting_table（留 1 個即可）
- bone、rotten_flesh、spider_eye 等無用戰利品
- 背包裡耐久度 ≤ 10% 的工具或裝備（幾乎已損壞，可以丟棄換空間）
  - 例外：若背包沒有同類型備用，且裝備欄對應槽位也是損壞的，則保留

【封口材料規則（重要）】
- cobblestone、cobbled_deepslate 是用來封埋垃圾的洞口材料
- 挖礦時：總共至少留 1 組（64個）cobblestone，超出的部分才能丟棄
- 其他活動：至少留 2 組（128個）

【根據活動決定的丟棄邏輯】

如果活動是 mining（挖礦）：
- cobblestone、cobbled_deepslate：留 1 組（64個），超出部分丟棄
- diorite、andesite、granite、tuff、gravel、flint、pointed_dripstone、dirt、sand：全部丟棄
- 低成本工具：可丟棄
- 釣魚垃圾（lily_pad、tripwire_hook 等）：全部丟棄

如果活動是 fishing、woodcutting、smelting 或其他：
- cobblestone、cobbled_deepslate：留 2 組（128個），超出部分丟棄
- diorite、andesite、granite、tuff：若超過 32 個可丟棄，否則保留
- gravel、flint、pointed_dripstone、dirt：全部丟棄
- 釣魚垃圾（lily_pad、tripwire_hook 等）：全部丟棄

如果沒有東西可以丟，回傳 continue。
items 清單填英文 item name，不包含數量。
"""


async def handle(state: dict, llm: LLMClient) -> dict | None:
    inventory = state.get("inventory", [])
    activity = state.get("activity", "idle")
    stack = state.get("stack", [])
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    y = round(pos.get("y", 0))
    chests = state.get("chests", [])

    def _fmt_inv_item(i):
        base = f"- {i['name']} x{i['count']}"
        pct = i.get('durability_pct')
        if pct is not None:
            base += f" (耐久 {pct}%)"
        return base
    inv_summary = "\n".join(_fmt_inv_item(i) for i in inventory)

    labeled_chests = [c for c in chests if c.get('label')]
    chests_summary = "\n".join(
        f"- id={c['id']} label={c.get('label','未分類')} freeSlots={c.get('freeSlots','?')}"
        for c in labeled_chests
    ) or "（無已分類箱子）"
    logs_count = sum(i['count'] for i in inventory if i['name'].endswith('_log'))
    planks_count = sum(i['count'] for i in inventory if i['name'].endswith('_planks'))
    # 1 chest = 8 planks, need 2 chests = 16 planks; logs convert 1:4 to planks
    wood_as_planks = planks_count + logs_count * 4
    can_make_chest = wood_as_planks >= 16
    if not labeled_chests:
        chests_summary += f"\n（背包中有木材：{logs_count} logs + {planks_count} planks = {wood_as_planks} planks 等效，{'足夠' if can_make_chest else '不足'}製作箱子（需要 16 planks））"

    # Current goal/progress from stack for mine remaining calculation
    top = stack[-1] if stack else {}
    goal = top.get("goal", {})
    progress = top.get("progress", {})
    goal_str = f"目標：{goal}，進度：{progress}" if goal else "（無目標）"

    equip_summary = equipment_summary(state)
    slots = state.get("inventory_slots") or {}
    slots_used = slots.get("used", len(inventory))
    slots_free = slots.get("free", 36 - len(inventory))

    prompt = (
        f"背包狀態：{slots_used}/36 格已用，剩餘 {slots_free} 格。機器人目前的活動：{activity}，位置 Y={y}，血量={health}/20，飢餓={food}/20。\n"
        f"當前任務：{goal_str}\n\n"
        f"目前裝備欄（耐久度）：\n{equip_summary}\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"已登記箱子：\n{chests_summary}\n\n"
        f"請根據活動規則決定處理方式。"
    )

    response = None
    try:
        print(f"[Skill/inventory] Prompt:\n{prompt}\n---")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = json.loads(clean)
        if decision.get('action') == 'plan':
            return decision  # handled by executor in agent.py
        return {"command": "inventory_decision", **decision}
    except Exception as e:
        print(f"[Skill/inventory] 解析失敗: {e}\n原始回應: {response!r}")
        return None
