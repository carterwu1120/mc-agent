"""
Canonical bot command reference.
Each entry describes one command the LLM can issue to the JS bot.
Use command_list(keys) to generate a formatted prompt section.
"""

COMMANDS: dict[str, dict] = {
    # ── Activities ────────────────────────────────────────────
    "mine":        {"desc": "挖礦",           "usage": "mine <ore> <count>",               "example": "mine iron 10、mine diamond 5"},
    "chop":        {"desc": "砍木頭",         "usage": "chop logs <count>",                "example": "chop logs 20"},
    "fish":        {"desc": "釣魚",           "usage": "fish catches <count>",             "example": "fish catches 30"},
    "smelt":       {"desc": "冶煉/烹飪",      "usage": "smelt <material> <count>",         "example": "smelt beef 6、smelt raw_iron 10（必須帶數量）"},
    "combat":      {"desc": "開始戰鬥",       "usage": "combat"},
    "hunt":        {"desc": "狩獵動物",       "usage": "hunt [count <n>]",             "example": "hunt count 5"},
    "getfood":     {"desc": "冶煉背包生食成熟食", "usage": "getfood [count <n>]",       "example": "getfood count 5"},
    "explore":     {"desc": "探索新區域",     "usage": "explore <target>",                 "example": "explore trees"},
    "surface":     {"desc": "移動到附近地表", "usage": "surface"},
    # ── Stop activities ───────────────────────────────────────
    "stopmine":    {"desc": "停止挖礦",       "usage": "stopmine"},
    "stopchop":    {"desc": "停止砍樹",       "usage": "stopchop"},
    "stopfish":    {"desc": "停止釣魚",       "usage": "stopfish"},
    "stopsmelt":   {"desc": "停止冶煉",       "usage": "stopsmelt"},
    "stopcombat":  {"desc": "停止戰鬥",       "usage": "stopcombat"},
    "stophunt":    {"desc": "停止狩獵",       "usage": "stophunt"},
    "stopgetfood": {"desc": "停止尋食",       "usage": "stopgetfood"},
    "stopsurface": {"desc": "停止移動到地表", "usage": "stopsurface"},
    "stopexplore": {"desc": "停止探索",       "usage": "stopexplore"},
    # ── Navigation ────────────────────────────────────────────
    "home":        {"desc": "傳送回基地",       "usage": "home"},
    "back":        {"desc": "返回上次活動位置", "usage": "back"},
    "come":        {"desc": "走向玩家",         "usage": "come [player]",                  "example": "come Carter"},
    "tp":          {"desc": "傳送到座標",       "usage": "tp <x> <y> <z>",                "example": "tp 10 64 -200"},
    # ── Equipment & inventory ─────────────────────────────────
    "equip":       {"desc": "裝備最佳武裝", "usage": "equip"},
    "deposit":     {"desc": "存入箱子",     "usage": "deposit <chest_id>",                 "example": "deposit 1"},
    "withdraw":    {"desc": "從箱子取出",   "usage": "withdraw <item> [count] <chest_id>", "example": "withdraw oak_log 16 1"},
    "makechest":   {"desc": "合成並放置大箱子（自動登記，完成後 chest_id 存入 {new_chest_id}）", "usage": "makechest"},
    "labelchest":  {"desc": "設定箱子類別", "usage": "labelchest <chest_id> <label>",      "example": "labelchest {new_chest_id} wood  （label: food/wood/stone/ore/misc）"},
    # ── Meta ──────────────────────────────────────────────────
    "chat":        {"desc": "傳送訊息給玩家", "usage": "chat"},
    "idle":        {"desc": "什麼都不做",     "usage": "idle"},
    "setmode":     {"desc": "切換操作模式",   "usage": "setmode <mode>",                   "example": "setmode survival"},
}


def command_list(keys: list[str]) -> str:
    """Return a formatted command reference string for use in LLM system prompts."""
    lines = []
    for key in keys:
        entry = COMMANDS.get(key)
        if not entry:
            continue
        usage = entry["usage"]
        desc = entry["desc"]
        example = entry.get("example", "")
        line = f"- {usage:<42} {desc}"
        if example:
            line += f"  例：{example}"
        lines.append(line)
    return "\n".join(lines)
