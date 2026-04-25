from agent.skills.commands_ref import command_list


SYSTEM_PROMPT = f"""你是 Minecraft 機器人的挖礦卡住處理助手。
機器人在挖礦時遇到障礙而中斷，請根據目前的 activity、卡住原因、是否存在未完成計畫，以及當前狀態決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{{"action": "replan", "commands": ["chop logs 4", "mine iron 3", "smelt raw_iron 3", "equip", "mine diamond 10"], "text": "...理由..."}}
{{"action": "skip", "text": "...理由..."}}
{{"command": "chop", "text": "...理由..."}}
{{"command": "mine", "args": ["iron", "8"], "text": "...理由..."}}
{{"command": "home", "text": "...理由..."}}
{{"command": "chat", "text": "...需要玩家幫助的說明..."}}
{{"command": "idle", "text": "...理由..."}}

【可用指令】
{command_list(["chop", "mine", "home", "chat", "idle"])}

決策原則：
- 若存在未完成 plan，且目前步驟是挖礦時因 no_tools 卡住，優先回覆 replan 或 skip，不要只回單一步驟 chop
- replan 必須是「從當前步驟開始的完整剩餘步驟」，可以插入修復步驟，但必須把原本剩餘計畫接回來
- 不要把 equip 當成萬用修復步驟；只有在前一步真的會產生新裝備（例如 smelt raw_iron 3 之後）時才加 equip
- 不要產生 equip、equip，或 chop 之後立刻接 equip 這種沒有新裝備可切換的序列
- 若原因為「無稿子且無法合成」：
  - 背包缺木材 → replan 插入 chop logs <n>，之後接回「補剛好夠用的工具鏈」與原剩餘步驟
  - 有木材但缺石稿/鐵鎬 → replan 插入補工具步驟，再接回原剩餘步驟
  - 補工具時採缺多少補多少，不要預設固定輸出 mine iron 16 / smelt raw_iron 16
- 若原因為 water_loop（礦道持續進水，機器人反覆掉入水中）或 trapped_in_water（連續多次完全無法逃脫水中）：
  - 有未完成計畫 → replan，在剩餘步驟前插入 "home"，讓機器人先回到安全位置再繼續
  - 無未完成計畫 → 回覆 home，讓機器人先撤離危險區域
- 只有在沒有未完成 plan、或這只是局部臨時修復時，才可以回單一步驟 chop / mine
- 若原因為「四個方向都被基岩或不可挖方塊阻擋，機器人可能被困住」→ 用 chat 告知玩家機器人被困，請玩家用 /tp 解救
- 其他情況 → idle
"""


def should_prefer_replan(reason: str, plan_context: dict | None) -> bool:
    return reason in ("no_tools", "water_loop", "trapped_in_water") and bool(plan_context)


def deterministic_shortcut(state: dict, plan_context: dict | None) -> list[dict] | None:
    reason = state.get("reason")

    if reason in ("water_loop", "trapped_in_water"):
        pending_steps = (plan_context or {}).get("pending_steps", [])
        current_cmd   = (plan_context or {}).get("current_cmd", "")
        if plan_context:
            cmds = ["home"]
            if current_cmd:
                cmds.append(current_cmd)
            cmds.extend(pending_steps)
            print(f"[Skill/activity_stuck] mining water_loop + plan → replan with home first: {cmds}")
            return [
                {"action": "replan", "commands": cmds, "text": "礦道持續進水，先回到安全位置再繼續計畫"},
            ]
        print("[Skill/activity_stuck] mining water_loop, no plan → home")
        return [{"command": "home", "text": "礦道持續進水，先撤回安全位置"}]

    if reason != "no_tools":
        return None
    caps = state.get("capabilities") or {}
    if not caps.get("can_make_pickaxe") or state.get("craft_issue_suspected"):
        return None

    pending_steps = (plan_context or {}).get("pending_steps", [])
    current_cmd = (plan_context or {}).get("current_cmd", "")
    new_cmds = ["equip"]
    if current_cmd:
        new_cmds.append(current_cmd)
    new_cmds.extend(pending_steps)
    print(f"[Skill/activity_stuck] mining no_tools + can_make_pickaxe → replan craft then retry: {new_cmds}")
    return [
        {"command": "chat", "text": "我有材料可以合成石鎬，合成後繼續挖礦"},
        {"action": "replan", "commands": new_cmds},
    ]
