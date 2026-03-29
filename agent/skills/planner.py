import json
import re
from agent.brain import LLMClient
from agent.skills.state_summary import summary_json

SYSTEM_PROMPT = """你是 Minecraft 機器人的任務規劃助手。
玩家用自然語言下達指令，你要轉換成機器人可執行的指令序列。
只能回覆以下其中一種 JSON（不含其他文字）：
{"action": "plan", "commands": ["chop logs 20", "mine iron 10"]}
{"action": "chat", "text": "我聽不懂你的意思"}

【可用指令與格式】
- mine <ore> <count>                     挖礦   例：mine diamond 10、mine iron 20
- chop logs <count>                      砍木頭 例：chop logs 20
- fish catches <count>                   釣魚   例：fish catches 30
- smelt <material>                       冶煉   例：smelt iron
- combat                                 開始戰鬥
- stopmine / stopchop / stopfish / stopsmelt / stopcombat  停止對應活動
- home                                   傳送回基地
- back                                   返回上次活動位置
- deposit <chest_id>                     存入箱子（需提供 chest id）
- withdraw <item> [count] <chest_id>     從箱子取出
- equip                                  裝備最佳武裝

【規則】
- 多個活動依序排入 commands 陣列
- 若當前有活動進行中（activity != idle），先加入對應 stop 指令再排新活動
- 玩家沒說數量時用合理預設值（木頭 32，礦石 16，釣魚 20）
- 玩家問問題、打招呼、或說的不是任務指令時，回傳 chat
- 只輸出 JSON，不要加任何解釋或其他文字
"""


async def handle(state: dict, llm: LLMClient) -> dict | None:
    message = state.get("message", "")
    activity = state.get("activity", "idle")
    pos = state.get("pos") or {}
    health = state.get("health", "?")
    food = state.get("food", "?")
    stack = state.get("stack", [])

    top = stack[-1] if stack else {}
    goal = top.get("goal", {})
    progress = top.get("progress", {})
    goal_str = f"目標：{goal}，進度：{progress}" if goal else "（無目標）"

    prompt = (
        f"玩家說：「{message}」\n\n"
        f"機器人目前狀態：活動={activity}，"
        f"位置=({pos.get('x',0):.0f}, {pos.get('y',0):.0f}, {pos.get('z',0):.0f})，"
        f"血量={health}/20，飢餓={food}/20。\n"
        f"當前任務：{goal_str}\n\n"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
        f"請根據玩家的話決定要做什麼。"
    )

    response = None
    try:
        print(f"[Planner] 玩家: {message}")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = json.loads(clean)

        if decision.get("action") == "plan":
            print(f"[Planner] 計畫: {decision.get('commands')}")
            return decision  # agent.py routes to executor

        if decision.get("action") == "chat":
            return {"command": "chat", "text": decision.get("text", "")}

    except Exception as e:
        print(f"[Planner] 解析失敗: {e}\n原始回應: {response!r}")

    return None
