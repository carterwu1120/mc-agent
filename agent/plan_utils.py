from __future__ import annotations


EQUIP_WORTHY_NEXT = {"mine", "combat", "hunt", "explore"}
EQUIPMENT_CHANGING = {"mine", "smelt", "withdraw", "makechest"}
NON_PROGRESS = {"chat", "idle"}


def command_name(cmd: str) -> str:
    parts = (cmd or "").split()
    return parts[0] if parts else ""


def normalize_commands(commands: list[str], previous_command: str | None = None) -> list[str]:
    normalized: list[str] = []
    last_non_chat = command_name(previous_command or "")

    for idx, raw in enumerate(commands):
        cmd = (raw or "").strip()
        if not cmd:
            continue

        name = command_name(cmd)
        next_name = ""
        for later in commands[idx + 1:]:
            later_name = command_name(later)
            if later_name and later_name not in NON_PROGRESS:
                next_name = later_name
                break

        if name == "equip":
            # Never keep repeated equip chains.
            if last_non_chat == "equip":
                continue
            # equip is only meaningful right before actions that use gear.
            if next_name not in EQUIP_WORTHY_NEXT:
                continue

        normalized.append(cmd)
        if name not in NON_PROGRESS:
            last_non_chat = name

    return normalized


def infer_step_reason(cmd: str, next_cmd: str | None = None) -> str:
    name = command_name(cmd)
    parts = cmd.split()

    if name == "chop":
        count = parts[2] if len(parts) >= 3 else "一些"
        return f"補充木材，目標 {count} 份"
    if name == "mine":
        target = parts[1] if len(parts) >= 2 else "資源"
        count = parts[2] if len(parts) >= 3 else "所需數量"
        return f"取得 {target}，目標 {count}"
    if name == "smelt":
        target = parts[1] if len(parts) >= 2 else "材料"
        count = parts[2] if len(parts) >= 3 else "所需數量"
        return f"冶煉 {target}，目標 {count}"
    if name == "hunt":
        count = parts[2] if len(parts) >= 3 else "所需數量"
        return f"先取得生食，目標 {count}"
    if name == "getfood":
        count = parts[2] if len(parts) >= 3 else "所需數量"
        return f"把生食處理成熟食，目標 {count}"
    if name == "fish":
        count = parts[2] if len(parts) >= 3 else "所需數量"
        return f"透過釣魚取得食物，目標 {count}"
    if name == "equip":
        next_name = command_name(next_cmd or "")
        if next_name == "mine":
            return "在挖礦前切換到最佳可用工具/裝備"
        if next_name == "combat":
            return "在戰鬥前切換到最佳可用裝備"
        if next_name == "hunt":
            return "在狩獵前切換到最佳可用武器"
        if next_name == "explore":
            return "在探索前整理目前裝備"
        return "更新目前裝備"
    if name == "surface":
        return "先回到較安全、可行動的地表"
    if name == "explore":
        target = parts[1] if len(parts) >= 2 else "新區域"
        return f"探索以尋找 {target}"
    if name == "home":
        return "返回基地"
    if name == "back":
        return "回到上一個活動位置"
    if name == "deposit":
        return "把物資存回箱子"
    if name == "withdraw":
        return "從箱子補充所需物資"
    if name == "makechest":
        return "新增箱子以整理物資"
    if name == "labelchest":
        return "標記箱子用途"
    if name == "come":
        return "移動到玩家身邊"
    if name == "chat":
        return "向玩家說明目前決策"
    return "執行這一步以推進整體目標"


def build_step_records(commands: list[str]) -> list[dict]:
    records: list[dict] = []
    for idx, cmd in enumerate(commands):
        next_cmd = commands[idx + 1] if idx + 1 < len(commands) else None
        records.append({
            "cmd": cmd,
            "reason": infer_step_reason(cmd, next_cmd),
            "status": "pending",
            "error": None,
        })
    return records
