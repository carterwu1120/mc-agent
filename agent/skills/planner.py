import asyncio
import json
import re
from agent.brain import LLMClient
from agent.skills.command_validation import (
    PLAN_ALLOWED_COMMANDS,
    build_reprompt_suffix,
    validate_commands,
)
from agent.skills.state_summary import summary_json
from agent.skills.commands_ref import command_list
from agent import task_memory
from agent.context_builder import (
    build_chests_summary,
    build_interrupted_tasks_section,
    build_recent_events_section,
    build_recent_failures_section,
)
from agent.plan_utils import normalize_commands
from agent.skills.llm_response import parse_llm_json

_PLANNER_ALLOWED_KEYS = [
    "mine", "chop", "fish", "smelt", "combat",
    "stopmine", "stopchop", "stopfish", "stopsmelt", "stopcombat", "stopsurface", "stopexplore",
    "home", "back", "surface", "explore",
    "deposit", "withdraw", "makechest", "labelchest", "equip", "come", "tp",
]
_PLANNER_COMMANDS = command_list(_PLANNER_ALLOWED_KEYS)

SYSTEM_PROMPT = f"""你是 Minecraft 機器人的任務規劃助手。
玩家用自然語言下達指令，你要轉換成機器人可執行的指令序列。
只能回覆以下其中一種 JSON（不含其他文字）：
{{"action": "plan", "goal": "簡短描述此次任務目標", "final_goal": "玩家的最終目標（若不明確或與前次相同可省略）", "reasoning": "為何這樣規劃（前置條件、缺少資源等）", "commands": ["chop logs 20", "mine iron 10"]}}
{{"action": "chat", "text": "我聽不懂你的意思"}}

【final_goal 說明】
- final_goal 是玩家本次對話的最終意圖（例如「建鑽石裝備」「建一棟房子」），跨多個子任務持續有效
- 若玩家明確提到最終目標（例如「我想要鑽石裝備」），設定 final_goal
- 若此次請求是前次 final_goal 的延續，可省略 final_goal（系統會自動繼承）
- 若玩家顯然換了目標，更新 final_goal
- 若玩家只是閒聊或問問題，省略 final_goal

【可用指令與格式】
{_PLANNER_COMMANDS}
- come [player]  走向玩家；只在需要用走路接近時使用
- tp <x> <y> <z>  傳送到指定座標；恢復任務時，若上次工作位置距離現在很遠，可用此指令先傳送回去
- tp <player>  傳送到指定玩家；若玩家叫你「過來 / come here / 來我這 / 來找我」，優先用這個

【規則】
- 只能使用「可用指令」清單中的指令，嚴禁發明清單以外的指令
- 若玩家要求的事情無法用清單指令完成（例如設定天氣、給予物品、傳送玩家、執行伺服器指令等），回傳 chat 說明無法執行
- 多個活動依序排入 commands 陣列，長度不限，根據實際需求決定
- 若當前有活動進行中（activity != idle），先加入對應 stop 指令再排新活動
- 若玩家只是在說停止、停下、先停、stop，優先規劃停止當前活動；若目前沒有活動就回 chat
- 若玩家明確要求你靠近他、過去找他、跟上他，優先規劃 tp <player>；只有在不適合傳送或玩家明確要求走過去時，才用 come
- 若玩家要求你回到地面、地表、陸地、上去，優先規劃 surface
- 玩家沒說數量時才用合理預設值（木頭 32，釣魚 20）；若是前置條件修復（例如缺工具、缺材料），請優先根據「缺多少補多少」來決定數量，不要一律固定用 16
- smelt 指令必須帶數量，不可省略，否則會把所有原料全部放入熔爐
- 玩家問問題、打招呼、或說的不是任務指令時，回傳 chat
- 只輸出 JSON，不要加任何解釋或其他文字

【執行中任務合併規則】
若玩家新需求與當前或剛被中斷的任務（待執行步驟中有 mine diamond 或 mine iron）使用相同資源：
- ⚠️ 絕對不要回傳 chat 說「沒有材料」或「背包沒有鑽石」——pending mine 步驟代表鑽石「即將取得」
- ⚠️ 不要看當前背包鑽石數量來判斷能不能做；要看 pending steps 的預期產出 + 背包現有量
- 輸出合併後的 plan（stopmine → mine 合併量 → equip）
- 合併量計算：
    1. 原任務 pending mine 量 = 待執行步驟中的 mine diamond X（X 已扣除當前進度）
    2. 新需求量 = 新裝備所需鑽石數
    3. 合併後 mine 量 = 原 pending + 新需求（不要從 0 重算）
    例：待執行步驟=["mine diamond 19","equip"]，新需求稿+劍=5 → ["stopmine","mine diamond 24","equip"]
- 若 pending steps 裡已有該資源的 mine，直接加量；不要新增重複的 mine 指令
- 若新需求的裝備可以在現有 equip 步驟一起完成，不要新增額外 equip
- 尊重步驟依賴順序：smelt 必須在對應 mine 之後，equip 必須在 mine/smelt 之後
- 若新需求有前置步驟需要比現有 pending 更早執行（例如先補食物），
  先讓當前步驟繼續（不 stop），把前置步驟排在 mine 合併步驟之前

【前置條件推理（重要）】
收到模糊或複合目標時，根據背包狀態（inventory）和裝備狀態（capabilities）自動推斷前置步驟，依序加入 commands：

【內建合成能力（重要）】
- bot 具備內建合成能力；某些指令在執行時，會自動合成完成該目標所需的中間物品
- 因此規劃時不能只停在「取得原料」，而要判斷「是否已經能真正完成玩家要的成品/結果」
- 若玩家要求的是某個成品、裝備、工具、箱子、熟食，且完成該目標需要先合成/製作，則 plan 必須包含能把目標真正做出來的最後一步，而不只是停在蒐集材料
- 換句話說：
  - 「做一套鑽石裝」不是只挖到鑽石，還要把裝備真正做出並穿上
  - 「做箱子」不是只有木材，還要 makechest
  - 「補食物」不是只有生食，還要 getfood / smelt 成熟食
- 若某步驟本身就會觸發內建合成（例如 equip 可能會把可製作的缺失裝備做出並穿上、makechest 會合成並放置箱子、getfood 會處理生食），可用該步驟作為收尾；不要把 plan 停在原料階段

裝備類（equip 使用規則）：
- equip（無參數）= 從背包自動換上最好的武器和護甲。挖礦活動**不需要** equip，因為採礦模組會自動切換到最佳鎬子。
- equip 只在以下情況才加入 plan：
  1. 玩家**明確要求**穿裝備（例如「穿上鑽石盔甲」「換好武器」）
  2. **前一步剛合成/冶煉出新的裝備/武器**，需要實際穿上（例如 smelt iron → craft iron_sword → equip）
  3. 要去打架（hunt/combat）且背包有**比目前手持更好的武器或護甲**
- ⚠️ 不要在 mine / chop / fish / smelt 前盲目加 equip：這些活動自己會處理所需工具
- ⚠️ 不要在缺乏對應裝備的情況下加 equip（例如背包沒有 diamond_pickaxe 就不要加 equip diamond_pickaxe）
- ⚠️ combat 不是前置步驟，絕對不要把 combat 加進 commands 作為準備動作
- 若食物不足（cooked_total < 5）：
  根據主目標決定目標熟食數量（food_target）：
  - 短暫任務（砍樹、存物品、來回跑腿）→ food_target = 8
  - 一般任務（挖鐵、挖石）→ food_target = 16
  - 長時間/危險任務（挖鑽石、下地深挖、combat、長途探索）→ food_target = 32
  取得方式：
  - 背包有生食（raw_total > 0）→ 先 getfood count <food_target>（自動冶煉）
  - 背包無生食 → 先 hunt count <food_target>，再 getfood count <food_target>
    （不要假設每隻動物會掉 2 個原料；hunt count 與熟食目標先採 1:1 的保守估計）
  - 不要使用 fish 作為前置步驟，除非玩家明確要求釣魚
- 若玩家是在「回去 / 恢復 / 繼續」先前的挖礦或挖鑽石任務，且背包熟食已至少 16 份，優先直接接回挖礦鏈；不要重新加入 hunt / getfood，除非玩家明確要求先補食物

工具鏈：
- 挖鑽石 → 需要鐵鎬（iron_pickaxe）→ 需要鐵錠 → 若背包無鐵錠：先補足做鐵鎬所需的鐵（通常 3 個 iron_ingot），再 mine diamond
- 挖金/鐵 → 需要石鎬（stone_pickaxe）→ 若無石頭工具：先補足做石鎬所需的 stone/cobblestone（通常 3 個），再 mine iron/gold
- 冶煉 → 需要燃料：**優先用木頭/木板**（oak_log, planks 等均可）→ 只有在背包無任何木頭且無煤炭時，才 mine coal；絕對不要在背包有木頭的情況下加 mine coal
- 製作箱子 → 需要木材 → 若木材不足：先 chop → 再 makechest
- 若只是為了補工具鏈，數量要保守精算：
  - 補 stone_pickaxe：mine stone 3（或略多一點 buffer）
  - 補 iron_pickaxe：mine iron 3 → smelt raw_iron 3（crafting.js 會自動用鐵錠合成鎬並裝備，不需加 equip）
  - 不要產生 chop → equip → smelt、或 equip → equip 這類沒有實際意義的序列
  - 不要在缺口明確時一律回 mine iron 16 / smelt raw_iron 16

裝備升級目標：
- 若玩家要求「做一套鑽石裝 / 鑽石盔甲 / diamond armor set」，目標不是只有拿到鑽石，而是最後要能穿上完整鑽石裝
- 先根據狀態摘要中的 armor_progress.diamond_missing / diamond_shortfall_for_full_set 計算缺口
- 若熟食不足，先補食物；若鑽石不足，加入 mine diamond <shortfall>
- 最後必須加入 equip，讓機器人把可製作的鑽石裝備做出並穿上；沒有 equip 不算完成這類目標
- 若已經有部分鑽石裝，鑽石數量只補缺口，不要固定一律 24

範例：
玩家說「幫我準備去挖鑽石」，背包無鐵鎬、食物只剩 2：
→ ["hunt count 32", "getfood count 32", "mine iron 3", "smelt raw_iron 3", "equip", "mine diamond 10"]
  （挖鑽石是長時間危險任務 → food_target=32；若只是為了先補鐵鎬，鐵的前置數量應接近實際缺口）

玩家說「我要釣魚」，背包無釣竿、有木材：
→ ["fish catches 20"]  ← bot 會自動製作釣竿，不需要手動加步驟

只在背包狀態確實缺少時才加前置步驟，不要過度規劃。

【箱子相關流程】
- 若已有對應類別的箱子（chests 資訊中有 label 且 freeSlots > 0）→ 直接 deposit <id>
- 若沒有對應箱子，但需要整理物品 → makechest 後接 labelchest + deposit：
  ["makechest", "labelchest {{new_chest_id}} <label>", "deposit {{new_chest_id}}"]
  {{new_chest_id}} 是佔位符，makechest 完成後自動填入，不要替換成數字
  label 根據要存入的物品類型：wood / ore / stone / misc / food

【未完成任務處理】
- prompt 中若有「未完成任務」區塊，表示之前的任務被中斷
- 若玩家說的是「繼續」、「繼續任務」、「continue」或語意上表示要恢復之前的工作，
  根據以下資訊決定最佳恢復策略：

  策略一：直接繼續（距離近、不需調整）
  → 直接回傳待執行步驟
  {{"action": "plan", "goal": "<原目標>", "commands": [<待執行步驟>]}}

  策略二：先傳送回去再繼續（上次工作位置與現在相差很遠，例如超過 100 格）
  → 在待執行步驟前加入 tp 指令
  {{"action": "plan", "goal": "<原目標>", "commands": ["tp <lastX> <lastY> <lastZ>", <待執行步驟>]}}

  策略三：重新規劃（物品不足、情況變化太大、原步驟不再合理）
  → 根據當前背包狀態重新規劃新的指令序列

- 不要重複已完成的步驟，直接從待執行的第一步開始
- tp 指令格式：tp <x> <y> <z>（使用整數座標，不要加小數點）
- 若玩家明確說「從這邊開始」、「在這裡開始」、「不要回去，直接在這挖」，
  代表要以目前位置重新開始，不要回上次工作位置；此時應視為 fresh task，而不是 resume
"""

RESUME_PATTERNS = [
    r"^\s*繼續\s*$",
    r"^\s*continue\s*$",
    r"^\s*resume\s*$",
    r"^\s*resumetask\s*$",
    r"^\s*繼續任務\s*$",
    r"^\s*繼續上次的\s*$",
    r"繼續.{0,6}任務",
    r"繼續.{0,6}中斷",
    r"接.{0,4}上次",
    r"接.{0,4}回來",
]

RESTART_FROM_HERE_PATTERNS = [
    r"從這邊開始",
    r"從這裡開始",
    r"在這邊開始",
    r"在這裡開始",
    r"就在這裡",
    r"就在這邊",
    r"不要回去",
    r"別回去",
    r"直接在這",
    r"直接在這裡",
    r"直接在這邊",
]

MINING_INTENT_PATTERNS = [
    r"挖礦",
    r"挖鐵",
    r"挖金",
    r"挖煤",
    r"挖鑽石",
    r"\bmine\b",
]

RETURN_TO_MINING_PATTERNS = [
    r"回去.*挖",
    r"回去.*mine",
    r"恢復.*挖",
    r"恢復.*mine",
    r"繼續.*挖",
    r"繼續.*mine",
    r"接著.*挖",
    r"接著.*mine",
    r"回來.*挖",
    r"回來.*mine",
]

COME_PATTERNS = [
    r"\bcome here\b",
    r"\bcome to me\b",
    r"過來",
    r"來我這",
    r"來我這裡",
    r"來找我",
    r"跟我來",
    r"跟上",
]

SURFACE_PATTERNS = [
    r"\bsurface\b",
    r"\bgo to surface\b",
    r"\bgo above ground\b",
    r"回到地面",
    r"回地表",
    r"到地面",
    r"到地表",
    r"上去",
    r"回到陸地",
]

STOP_PATTERNS = [
    r"^\s*stop\s*$",
    r"^\s*停止\s*$",
    r"^\s*停下(來)?\s*$",
    r"^\s*先停(下來)?\s*$",
    r"^\s*不要做了\s*$",
    r"^\s*暫停\s*$",
    r"^\s*先暫停\s*$",
    r"^\s*pause\s*$",
]

_TRANSIENT_LLM_ERROR_PATTERNS = (
    "503",
    "unavailable",
    "high demand",
    "try again later",
)


def _is_transient_llm_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return any(pattern in text for pattern in _TRANSIENT_LLM_ERROR_PATTERNS)


async def _chat_with_retry(llm: LLMClient, prompt: str, system: str, attempts: int = 3) -> str:
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return await llm.chat(
                [{"role": "user", "content": prompt}],
                system=system,
            )
        except Exception as e:
            last_error = e
            if not _is_transient_llm_error(e) or attempt == attempts:
                raise
            delay = attempt
            print(f"[Planner] LLM 暫時不可用，第 {attempt}/{attempts} 次失敗，{delay}s 後重試: {e}")
            await asyncio.sleep(delay)
    assert last_error is not None
    raise last_error


def _parse_decision_text(response: str) -> dict:
    clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
    clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
    raw = json.loads(clean)
    return parse_llm_json(raw, "Planner")


def _planner_failure_chat() -> dict:
    return {"command": "chat", "text": "我這次規劃失敗了，請再說一次。"}


EQUIPMENT_ITEM_COSTS = {
    "helmet": 5,
    "chestplate": 8,
    "leggings": 7,
    "boots": 4,
    "sword": 2,
    "pickaxe": 3,
    "axe": 3,
    "shovel": 1,
    "hoe": 2,
}

EQUIPMENT_ALIASES = {
    "diamond": ("鑽石", "diamond"),
    "iron": ("鐵", "iron"),
    "stone": ("石", "stone"),
}

ARMOR_SET_ITEMS = ("helmet", "chestplate", "leggings", "boots")
TOOL_SET_ITEMS = ("pickaxe", "axe", "shovel", "hoe")


def _inventory_counts(state: dict) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in (state.get("inventory") or []):
        name = item.get("name")
        if not name:
            continue
        counts[name] = counts.get(name, 0) + int(item.get("count", 0) or 0)
    return counts


def _matches_phrase(message: str, lowered: str, phrase: str) -> bool:
    if re.fullmatch(r"[a-z ]+", phrase):
        return re.search(rf"\b{re.escape(phrase)}\b", lowered) is not None
    return phrase in message or phrase in lowered


def _owned_equipment_items(state: dict, material: str) -> set[str]:
    owned: set[str] = set()
    inventory_counts = _inventory_counts(state)
    for name, count in inventory_counts.items():
        if count <= 0 or not name.startswith(f"{material}_"):
            continue
        for suffix in EQUIPMENT_ITEM_COSTS:
            if name.endswith(f"_{suffix}"):
                owned.add(suffix)
                break

    equipment = state.get("equipment") or {}
    main_hand = equipment.get("main_hand")
    if isinstance(main_hand, dict):
        name = main_hand.get("name")
        if name and name.startswith(f"{material}_"):
            for suffix in EQUIPMENT_ITEM_COSTS:
                if name.endswith(f"_{suffix}"):
                    owned.add(suffix)
                    break

    for piece in ((equipment.get("armor") or {}).values()):
        if not isinstance(piece, dict):
            continue
        name = piece.get("name")
        if name and name.startswith(f"{material}_"):
            for suffix in EQUIPMENT_ITEM_COSTS:
                if name.endswith(f"_{suffix}"):
                    owned.add(suffix)
                    break
    return owned


def _requested_equipment_items(message: str, lowered: str) -> list[str]:
    requested: list[str] = []

    armor_patterns = (
        "鑽石裝", "鑽石裝備", "鑽石盔甲", "一套鑽石裝",
        "鐵裝", "鐵裝備", "鐵盔甲", "一套鐵裝",
        "diamond armor", "diamond armour", "diamond armor set",
        "iron armor", "iron armour", "iron armor set",
    )
    if any(_matches_phrase(message, lowered, p) for p in armor_patterns):
        requested.extend(ARMOR_SET_ITEMS)

    tool_set_patterns = (
        "工具組", "工具套", "全套工具", "一套工具",
        "diamond tools", "iron tools", "stone tools", "tool set",
    )
    if any(_matches_phrase(message, lowered, p) for p in tool_set_patterns):
        requested.extend(TOOL_SET_ITEMS)

    item_patterns = (
        ("sword", ("劍", "sword")),
        ("pickaxe", ("稿", "鎬", "稿子", "pickaxe")),
        ("axe", ("斧", "axe")),
        ("shovel", ("鏟", "shovel")),
        ("hoe", ("鋤", "hoe")),
    )
    for item, patterns in item_patterns:
        if any(_matches_phrase(message, lowered, p) for p in patterns):
            requested.append(item)

    seen: set[str] = set()
    deduped: list[str] = []
    for item in requested:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


def _running_task_pending_mine_count(material: str) -> int:
    """Returns total pending mine count for the given material in the current or just-interrupted task.

    Also checks "interrupted" because task_arbitration interrupts the task before planner runs,
    so by the time planner is called the status is already "interrupted".
    """
    task = task_memory.load_any()
    if not task or task.get("status") not in ("running", "interrupted"):
        return 0
    for cmd in _task_remaining_commands(task):
        parts = (cmd or "").split()
        if len(parts) >= 3 and parts[0] == "mine" and parts[1] == material:
            try:
                return int(parts[2])
            except (ValueError, IndexError):
                pass
    return 0


def _equipment_goal_shortcut(message: str, state: dict, activity: str) -> dict | None:
    lowered = message.lower()
    material = next(
        (name for name, patterns in EQUIPMENT_ALIASES.items() if any(_matches_phrase(message, lowered, p) for p in patterns)),
        None,
    )
    if not material:
        return None

    requested_items = _requested_equipment_items(message, lowered)
    if not requested_items:
        return None
    if material == "stone" and any(item in ARMOR_SET_ITEMS for item in requested_items):
        return None

    # If there's a running task that already has pending mine steps for the same
    # material, skip the shortcut and let the LLM merge the tasks properly.
    pending_count = _running_task_pending_mine_count(material) if material in ("diamond", "iron") else 0
    print(f"[Planner] equipment shortcut: material={material}, pending_mine={pending_count}")
    if pending_count > 0:
        print(f"[Planner] 偵測到 running task 有 pending mine {material}，跳過 shortcut → LLM 合併")
        return None

    summary = json.loads(summary_json(state))
    cooked_total = int(((((summary.get("resources") or {}).get("food") or {}).get("cooked_total")) or 0))
    materials = ((summary.get("resources") or {}).get("materials") or {})
    owned_items = _owned_equipment_items(state, material)
    missing_items = [item for item in requested_items if item not in owned_items]
    needed_units = sum(EQUIPMENT_ITEM_COSTS[item] for item in missing_items)

    commands: list[str] = []
    stop_cmd = _stop_command_for_activity(activity)
    if activity not in (None, "idle") and stop_cmd:
        commands.append(stop_cmd)

    # Keep armor-goal shortcut consistent with the planner's general food rule:
    # only add a food-prep step when cooked food is actually low, rather than
    # always topping up to 32 for every armor request.
    if cooked_total < 5:
        commands.append("getfood count 32")

    if material == "diamond":
        have_units = int(materials.get("diamond", 0) or 0)
        shortfall = max(0, needed_units - have_units)
        if shortfall > 0:
            commands.append(f"mine diamond {shortfall}")
    elif material == "iron":
        iron_ingot = int(materials.get("iron_ingot", 0) or 0)
        raw_iron = int(materials.get("raw_iron", 0) or 0)
        total_iron_units = iron_ingot + raw_iron
        shortfall = max(0, needed_units - total_iron_units)
        if shortfall > 0:
            commands.append(f"mine iron {shortfall}")
        smelt_count = max(0, needed_units - iron_ingot)
        if smelt_count > 0:
            commands.append(f"smelt raw_iron {smelt_count}")
    elif material == "stone":
        cobblestone = int(materials.get("cobblestone", 0) or 0)
        shortfall = max(0, needed_units - cobblestone)
        if shortfall > 0:
            commands.append(f"mine stone {shortfall}")

    commands.append("equip")

    goal_material = {
        "diamond": "鑽石",
        "iron": "鐵",
        "stone": "石",
    }[material]
    goal_labels = {
        "helmet": "頭盔",
        "chestplate": "胸甲",
        "leggings": "護腿",
        "boots": "靴子",
        "sword": "劍",
        "pickaxe": "稿",
        "axe": "斧",
        "shovel": "鏟",
        "hoe": "鋤",
    }
    goal_text = f"製作{goal_material}" + "、".join(goal_labels[item] for item in requested_items)

    return {
        "action": "plan",
        "goal": goal_text,
        "commands": normalize_commands(commands),
    }


async def _reprompt_invalid_plan(
    llm: LLMClient,
    prompt: str,
    invalid_commands: list[str],
    errors,
) -> dict | None:
    reprompt = prompt + build_reprompt_suffix(
        invalid_commands=invalid_commands,
        errors=errors,
        allowed_command_keys=_PLANNER_ALLOWED_KEYS,
    )
    try:
        print(f"[Planner] 偵測到非法計畫，重問一次 LLM：{invalid_commands}")
        corrected = await _chat_with_retry(llm, reprompt, SYSTEM_PROMPT)
        decision = _parse_decision_text(corrected)
        if decision.get("action") != "plan":
            return None
        correction_errors = validate_commands(
            decision.get("commands", []),
            allowed_commands=PLAN_ALLOWED_COMMANDS,
        )
        if correction_errors:
            print(f"[Planner] 修正後計畫仍不合法：{[e.command for e in correction_errors]}")
            return None
        print(f"[Planner] 修正後計畫: {decision.get('commands')}")
        return decision
    except Exception as e:
        print(f"[Planner] 修正計畫失敗: {e}")
        return None


def _stop_command_for_activity(activity: str) -> str | None:
    stop_map = {
        "fishing": "stopfish",
        "chopping": "stopchop",
        "mining": "stopmine",
        "smelting": "stopsmelt",
        "surface": "stopsurface",
        "explore": "stopexplore",
        "combat": "stopcombat",
        "hunting": "stophunt",
        "getfood": "stopgetfood",
    }
    return stop_map.get(activity)


def _distance_sq(a: dict | None, b: dict | None) -> float:
    ax = float((a or {}).get("x", 0.0))
    ay = float((a or {}).get("y", 0.0))
    az = float((a or {}).get("z", 0.0))
    bx = float((b or {}).get("x", 0.0))
    by = float((b or {}).get("y", 0.0))
    bz = float((b or {}).get("z", 0.0))
    return (ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2


def _maybe_plan_resume(message: str, state: dict) -> dict | None:
    if not any(re.search(p, message, re.IGNORECASE) for p in RESUME_PATTERNS):
        return None

    task = task_memory.load()
    resume_current = bool(task and task.get("status") == "interrupted")
    if not resume_current:
        task = task_memory.latest_interrupted()
    if not task:
        return {"command": "chat", "text": "目前沒有可恢復的中斷任務。"}

    steps = task.get("steps", [])
    current_step = task.get("currentStep", 0)
    remaining = [
        s["cmd"] for s in steps[current_step:]
        if s.get("status") not in ("done", "failed")
    ] or task.get("commands", [])[current_step:]

    if not remaining:
        return {"command": "chat", "text": "目前沒有可恢復的步驟。"}

    task_context = task.get("context") or {}
    work_pos = task_context.get("workPos") or task_context.get("currentPos")
    if not work_pos:
        for step in reversed(steps):
            sc = step.get("context") or {}
            work_pos = sc.get("workPos") or sc.get("currentPos")
            if work_pos:
                break

    commands = list(remaining)
    current_pos = state.get("pos") or {}
    if work_pos and _distance_sq(current_pos, work_pos) > 100 ** 2:
        commands = [f"tp {round(work_pos['x'])} {round(work_pos['y'])} {round(work_pos['z'])}"] + commands

    return {
        "action": "plan",
        "goal": task.get("goal", "恢復中斷任務"),
        "commands": commands,
        "resume_task": resume_current,
    }


def _is_resume_message(message: str) -> bool:
    return any(re.search(p, message, re.IGNORECASE) for p in RESUME_PATTERNS)


def _is_restart_from_here_message(message: str) -> bool:
    return any(re.search(p, message, re.IGNORECASE) for p in RESTART_FROM_HERE_PATTERNS)


def _is_mining_intent_message(message: str) -> bool:
    lowered = message.lower()
    return any(re.search(p, lowered if p.startswith(r"\b") else message, re.IGNORECASE) for p in MINING_INTENT_PATTERNS)


def _is_return_to_recent_mining_message(message: str) -> bool:
    if _is_restart_from_here_message(message):
        return False
    if not _is_mining_intent_message(message):
        return False
    return any(re.search(p, message, re.IGNORECASE) for p in RETURN_TO_MINING_PATTERNS)


def _food_summary(state: dict) -> dict:
    summary = json.loads(summary_json(state))
    return ((summary.get("resources") or {}).get("food") or {})


def _has_sufficient_food_for_resume(state: dict, threshold: int = 16) -> bool:
    food = _food_summary(state)
    cooked_total = int(food.get("cooked_total", 0) or 0)
    return cooked_total >= threshold


def _strip_food_prep_prefix(commands: list[str], keep_stop_prefix: bool = True) -> list[str]:
    if not commands:
        return []

    kept: list[str] = []
    index = 0
    if keep_stop_prefix:
        while index < len(commands):
            head = (commands[index] or "").strip()
            if not head.startswith("stop"):
                break
            kept.append(head)
            index += 1

    while index < len(commands):
        cmd = (commands[index] or "").strip()
        if not cmd:
            index += 1
            continue
        if cmd.startswith("hunt ") or cmd.startswith("getfood ") or cmd.startswith("fish "):
            index += 1
            continue
        break

    return kept + [cmd for cmd in commands[index:] if cmd]


def _task_remaining_commands(task: dict | None) -> list[str]:
    if not task:
        return []
    steps = task.get("steps", [])
    current_step = int(task.get("currentStep", 0) or 0)
    remaining = [
        s["cmd"] for s in steps[current_step:]
        if s.get("status") not in ("done", "failed")
    ] or task.get("commands", [])[current_step:]
    return list(remaining)


def _find_recent_mining_task() -> dict | None:
    current = task_memory.load_any()
    candidates: list[dict] = []
    if current:
        candidates.append(current)
    candidates.extend(task_memory.interrupted_tasks())
    for task in candidates:
        remaining = _task_remaining_commands(task)
        if any((cmd or "").split()[:1] == ["mine"] for cmd in remaining):
            return task
    return None


def _maybe_plan_restart_from_here(message: str, state: dict) -> dict | None:
    if not _is_restart_from_here_message(message):
        return None
    if not _is_mining_intent_message(message):
        return None

    task = _find_recent_mining_task()
    if not task:
        return None

    commands = _task_remaining_commands(task)
    if not commands:
        return None

    commands = [cmd for cmd in commands if not cmd.startswith("tp ")]
    if not commands:
        return None

    activity = state.get("activity", "idle")
    stop_cmd = _stop_command_for_activity(activity)
    if activity not in (None, "idle") and stop_cmd:
        commands = [stop_cmd] + commands

    return {
        "action": "plan",
        "goal": task.get("goal", "從目前位置重新開始任務"),
        "commands": normalize_commands(commands),
        "resume_task": False,
    }


def _maybe_plan_return_to_recent_mining(message: str, state: dict) -> dict | None:
    if not _is_return_to_recent_mining_message(message):
        return None

    task = _find_recent_mining_task()
    if not task:
        return None

    commands = _task_remaining_commands(task)
    if not commands:
        return None

    if _has_sufficient_food_for_resume(state, threshold=16):
        commands = _strip_food_prep_prefix(commands)

    if not commands:
        return None

    activity = state.get("activity", "idle")
    stop_cmd = _stop_command_for_activity(activity)
    if activity not in (None, "idle") and stop_cmd and commands[0] != stop_cmd:
        commands = [stop_cmd] + commands

    return {
        "action": "plan",
        "goal": task.get("goal", "恢復近期挖礦任務"),
        "commands": normalize_commands(commands),
        "resume_task": False,
    }


def _maybe_plan_come(message: str, activity: str, player_name: str | None) -> dict | None:
    lowered = message.lower()
    if not any(re.search(pattern, lowered if pattern.startswith(r"\b") else message) for pattern in COME_PATTERNS):
        return None

    commands: list[str] = []
    stop_map = {
        "fishing": "stopfish",
        "chopping": "stopchop",
        "mining": "stopmine",
        "smelting": "stopsmelt",
        "surface": "stopsurface",
        "explore": "stopexplore",
        "combat": "stopcombat",
        "hunting": "stophunt",
        "getfood": "stopgetfood",
    }
    stop_cmd = stop_map.get(activity)
    if stop_cmd:
        commands.append(stop_cmd)

    if player_name:
        commands.append(f"tp {player_name}")
    else:
        commands.append("come")

    return {"action": "plan", "commands": commands}


def _maybe_plan_surface(message: str, activity: str) -> dict | None:
    lowered = message.lower()
    if not any(re.search(pattern, lowered if pattern.startswith(r"\b") else message) for pattern in SURFACE_PATTERNS):
        return None

    commands: list[str] = []
    stop_map = {
        "fishing": "stopfish",
        "chopping": "stopchop",
        "mining": "stopmine",
        "smelting": "stopsmelt",
        "surface": "stopsurface",
        "explore": "stopexplore",
        "combat": "stopcombat",
        "hunting": "stophunt",
        "getfood": "stopgetfood",
    }
    stop_cmd = stop_map.get(activity)
    if stop_cmd:
        commands.append(stop_cmd)
    commands.append("surface")
    return {"action": "plan", "commands": commands}


def _maybe_plan_stop(message: str, activity: str) -> dict | None:
    if not any(re.search(pattern, message.lower() if pattern.startswith(r"^\s*stop") else message) for pattern in STOP_PATTERNS):
        return None
    stop_cmd = _stop_command_for_activity(activity)
    # 標記任務為中斷，讓之後說「繼續」能接回
    task_memory.interrupt("player_stop")
    if not stop_cmd:
        return {"command": "chat", "text": "已暫停。說「繼續」可接回任務。"}
    return {"action": "plan", "commands": [stop_cmd]}


async def handle(state: dict, llm: LLMClient) -> dict | None:
    message = state.get("message", "")
    player_name = state.get("from")
    activity = state.get("activity", "idle")
    mode = state.get("mode", "survival")
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    stack = state.get("stack", [])
    chests = state.get("chests", [])

    top = stack[-1] if stack else {}
    goal = top.get("goal", {})
    progress = top.get("progress", {})
    goal_str = f"目標：{goal}，進度：{progress}" if goal else "（無目標）"

    prev_task = task_memory.load_any()
    task_ctx = ""
    remaining_cmds_for_resume = []
    if prev_task:
        task_status = prev_task.get("status", "unknown")
        steps = prev_task.get("steps", [])
        done_steps = [s["cmd"] for s in steps if s["status"] == "done"]
        remaining_cmds_for_resume = [s["cmd"] for s in steps if s["status"] not in ("done", "failed")] \
            if task_status in ("running", "interrupted") else []

        # Extract last known work position from task context
        task_context = prev_task.get("context") or {}
        work_pos = task_context.get("workPos") or task_context.get("currentPos")
        if not work_pos:
            for s in reversed(steps):
                sc = s.get("context") or {}
                work_pos = sc.get("workPos") or sc.get("currentPos")
                if work_pos:
                    break
        work_pos_str = ""
        if work_pos:
            work_pos_str = f"\n上次工作位置：({work_pos.get('x', 0):.0f}, {work_pos.get('y', 0):.0f}, {work_pos.get('z', 0):.0f})"

        final_goal = prev_task.get("final_goal")
        final_goal_str = f"\n最終目標：{final_goal}" if final_goal else ""

        _STATUS_LABEL = {
            "running":     "【執行中任務】",
            "interrupted": "【未完成任務】",
            "done":        "【前次完成任務】",
            "failed":      "【前次失敗任務】",
        }
        label = _STATUS_LABEL.get(task_status, f"【任務({task_status})】")

        pending_note = ""
        if task_status in ("running", "interrupted") and remaining_cmds_for_resume:
            pending_note = "（注意：待執行步驟的資源視為「即將取得」，規劃時不要因背包暫時沒有而說無法完成）\n"

        task_ctx = (
            f"\n\n{label}目標：{prev_task['goal']}{final_goal_str}\n"
            f"已完成步驟：{done_steps or '（無）'}\n"
            + (f"待執行步驟：{remaining_cmds_for_resume}\n{pending_note}" if remaining_cmds_for_resume else "")
            + work_pos_str
        )

    recent_events_section = build_recent_events_section(task_memory.recent_events(), limit=6)
    recent_failures_section = build_recent_failures_section(task_memory.recent_failures(), limit=4)
    interrupted_tasks_section = build_interrupted_tasks_section(task_memory.interrupted_tasks(), limit=2)
    chests_summary = build_chests_summary(chests, max_chests=4, max_items=4)

    prompt = (
        f"玩家說：「{message}」\n\n"
        f"機器人目前狀態：活動={activity}，模式={mode}，"
        f"位置=({pos.get('x',0):.0f}, {pos.get('y',0):.0f}, {pos.get('z',0):.0f})，"
        f"血量={health}/20，飢餓={food}/20。\n"
        f"當前任務：{goal_str}{task_ctx}\n\n"
        f"已登記箱子：\n{chests_summary}\n\n"
        f"{interrupted_tasks_section}"
        f"{recent_events_section}"
        f"{recent_failures_section}"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
        f"請根據玩家的話決定要做什麼。"
    )

    response = None
    try:
        print(f"[Planner] 玩家: {message}")

        shortcut = _maybe_plan_restart_from_here(message, state)
        if shortcut:
            print(f"[Planner] 就地重開任務快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_return_to_recent_mining(message, state)
        if shortcut:
            print(f"[Planner] 回去挖礦快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_resume(message, state)
        if shortcut:
            if shortcut.get("action") == "plan":
                print(f"[Planner] 恢復任務快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_come(message, activity, player_name)
        if shortcut:
            print(f"[Planner] 快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _equipment_goal_shortcut(message, state, activity)
        if shortcut:
            print(f"[Planner] 裝備目標快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_surface(message, activity)
        if shortcut:
            print(f"[Planner] 快捷規劃: {shortcut.get('commands')}")
            return shortcut
        shortcut = _maybe_plan_stop(message, activity)
        if shortcut:
            print(f"[Planner] 快捷規劃: {shortcut.get('commands')}")
            return shortcut
        response = await _chat_with_retry(llm, prompt, SYSTEM_PROMPT)
        decision = _parse_decision_text(response)

        if decision.get("action") == "plan":
            commands = normalize_commands(decision.get("commands", []))
            errors = validate_commands(commands, allowed_commands=PLAN_ALLOWED_COMMANDS)
            if errors:
                repaired = await _reprompt_invalid_plan(
                    llm,
                    prompt,
                    invalid_commands=[error.command for error in errors],
                    errors=errors,
                )
                if repaired:
                    return repaired
                return _planner_failure_chat()
            decision["commands"] = commands
            print(f"[Planner] 計畫: {decision.get('commands')}")
            return decision  # agent.py routes to executor

        if decision.get("action") == "chat":
            return {"command": "chat", "text": decision.get("text", "")}

    except Exception as e:
        print(f"[Planner] 解析失敗: {e}\n原始回應: {response!r}")

    return _planner_failure_chat()
