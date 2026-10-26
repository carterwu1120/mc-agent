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
    "smelt":       {"desc": "冶煉/烹飪，必須帶數量", "usage": "smelt <material> <count>",   "example": "smelt beef 6、smelt raw_iron 10"},
    "combat":      {"desc": "開始戰鬥",       "usage": "combat"},
    "hunt":        {"desc": "狩獵動物",       "usage": "hunt count <n>",               "example": "hunt count 5"},
    "getfood":     {"desc": "冶煉背包生食成熟食", "usage": "getfood count <n>",         "example": "getfood count 5"},
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
    # ── Navigation ────────────────────────────────────────────
    "back":        {"desc": "回到目前活動的起始位置", "usage": "back"},
    "come":        {"desc": "走向玩家",         "usage": "come [player]",                  "example": "come Carter"},
    "tp":          {"desc": "傳送到座標或玩家", "usage": "tp <x> <y> <z> | tp <player>",  "example": "tp 10 64 -200、tp Carter"},
    # ── Equipment & inventory ─────────────────────────────────
    "equip":       {"desc": "裝備背包現有武裝，不合成", "usage": "equip"},
    "makearmor":   {"desc": "用背包現有鐵錠/鑽石合成並穿上盔甲，只補空、快壞或較差的部位；不採集不冶煉，不足時回報缺多少", "usage": "makearmor <iron|diamond>"},
    "preparetool": {"desc": "用背包現有材料（含工作台、木棍）做出至少該等級的鎬子，不採集（tier: wooden/stone/iron/diamond/netherite）", "usage": "preparetool <tier>_pickaxe"},
    # ── Meta ──────────────────────────────────────────────────
    "chat":        {"desc": "傳送訊息給玩家", "usage": "chat"},
    "idle":        {"desc": "什麼都不做",     "usage": "idle"},
    "setmode":     {"desc": "切換操作模式",   "usage": "setmode <mode>",                   "example": "setmode survival"},
}


def command_list(keys, *, include_stops: bool = True) -> str:
    """Return a formatted command reference string for use in LLM system prompts.

    include_stops=False omits stop* commands for prompts that plan after the old
    activity has already been halted (recovery, respawn, prerequisite plans).
    """
    lines = []
    for key in keys:
        if not include_stops and key.startswith("stop"):
            continue
        entry = COMMANDS.get(key)
        if not entry:
            continue
        usage = entry["usage"]
        desc = entry["desc"]
        example = entry.get("example", "")
        line = f"- {usage}：{desc}"
        if example:
            line += f"（例：{example}）"
        lines.append(line)
    return "\n".join(lines)
