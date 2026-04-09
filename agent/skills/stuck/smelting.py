from agent.skills.commands_ref import command_list


SYSTEM_PROMPT = f"""你是 Minecraft 機器人的燒製卡住處理助手。
機器人在燒製過程中遇到問題而中斷，請根據當前資源與整體計畫目標決定下一步。
每個回覆都必須包含 "text" 欄位說明你的決策理由（一句話，繁體中文）。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{{"action": "replan", "commands": ["chop logs 8", "smelt <target> <count>"], "text": "...理由..."}}
{{"action": "skip", "text": "...理由..."}}
{{"command": "chop", "goal": {{"logs": 8}}, "text": "...理由..."}}
{{"command": "mine", "args": ["<target>", "<count>"], "text": "...理由..."}}
{{"command": "home", "text": "...理由..."}}
{{"command": "withdraw", "args": ["oak_log", "16", "1"], "text": "...理由..."}}
{{"command": "chat", "text": "...提醒內容..."}}
{{"command": "idle", "text": "...理由..."}}

【可用指令】
{command_list(["mine", "chop", "home", "withdraw", "chat", "idle"])}

【no_fuel 決策邏輯（背包沒有任何可用燃料）】
先看整體計畫目標（plan_context.goal）和剩餘步驟（pending_steps）：

情境 A：下一步是挖礦（pending_steps 含 mine iron/diamond/coal 等）
→ 挖礦途中幾乎必定挖到煤礦 → 直接 skip 這個冶煉步驟，挖到煤後可以繼續
→ 使用 {{"action": "skip"}}

情境 B：背包有木頭（inventory 有 oak_log / planks）但量不夠燒完全部
→ 先用現有木頭燒一部分，剩下等挖礦拿到煤再繼續
→ replan：["chop logs <N>", "smelt <target> <count>"] 或直接 skip 讓挖礦途中解決

情境 C：計畫不含挖礦、背包也沒有木頭
→ 去砍樹取得燃料再繼續冶煉
→ replan：["chop logs 8", "smelt <target> <count>"]

【no_progress 決策邏輯（熔爐無法放置或燒製沒有任何進展）】
先確認上層活動堆疊（prompt 中「上層活動」段落）：

情境 A：上層是挖礦（mining diamond/iron 等），冶煉是可能是為了補工具（smelt iron）
→ 這是必要的前置步驟，不能 skip，否則挖礦也無法繼續
→ replan 重試：["smelt <target> <count>", "<parent mining cmd>", ...原計畫剩餘步驟]
→ 例：parent goal={{"target":"diamond","count":20}} → replan: ["smelt iron 3", "mine diamond 20", "equip"]

情境 B：冶煉是頂層任務（無上層活動），反覆放不了熔爐
→ replan 重試一次或 skip

【missing_dependency / no_input 決策邏輯】
- 若 missing 包含 wood → chop（估算 goal.logs 數量）
- 若 missing 是 cobblestone → mine stone <missing_count>
- 若背包有 iron_ingot >= 3 但沒有 iron_pickaxe → chat 提醒玩家合成
- 若背包資源足夠 → mine diamond
- 其他情況 → idle
- 禁止回覆 fish、smelt
"""


def looks_like_getfood_subflow(reason: str, plan_context: dict | None) -> bool:
    if reason != "no_input" or not plan_context:
        return False
    current_cmd = (plan_context.get("current_cmd") or "").strip()
    return current_cmd.startswith("getfood ")


def deterministic_shortcut(state: dict, plan_context: dict | None, build_getfood_replan_from_smelting) -> list[dict] | None:
    reason = state.get("reason")
    if not looks_like_getfood_subflow(reason, plan_context):
        return None
    shortcut = build_getfood_replan_from_smelting(state, plan_context or {})
    if shortcut:
        print("[Skill/activity_stuck] smelting/no_input 發生在 getfood 子流程，直接改走補食物 replan")
    return shortcut
