import json
from agent.brain import LLMClient

SYSTEM_PROMPT = """你是 Minecraft 機器人的釣魚決策助手。
你會收到釣魚卡住的狀態與周圍地圖，請決定下一步行動。
只能回覆以下其中一種 JSON（不要加任何其他文字）：
{"action": "move", "x": 102, "z": -45}
{"action": "stop"}

地圖說明：B=Bot目前位置, W=水, .=可走的陸地, #=阻擋, ~=懸崖
移動規則：
- 從地圖中選一個 . 格，直接回傳該格的世界座標 x, z
- 目標必須是 . 格，絕對不能選 W、#、~ 格
- 選靠近 W 的 . 格，這樣移動後才能拋竿到水裡
- stop：附近根本沒有合適的水域時才用
- 可走的路地和水之間如果有阻擋的話，那表示可能要繞過阻擋，走道更靠近水的可走陸地
"""


def build_prompt(state: dict) -> str:
    pos = state.get("pos", {})
    water = state.get("waterTarget")
    map_data = state.get("areaMap")

    map_section = "（無地圖資料）"
    if isinstance(map_data, dict) and "grid" in map_data:
        # 新格式：帶座標的 object
        grid = map_data["grid"]
        origin_x = map_data["originX"]
        origin_z = map_data["originZ"]
        x_labels = "     " + "".join(f"{origin_x + i:2d}" for i in range(len(grid[0])))
        rows = [x_labels]
        for i, row in enumerate(grid):
            z = origin_z + i
            rows.append(f"{z:4d}: {''.join(f' {c}' for c in row)}")
        map_section = '\n'.join(rows)
    elif isinstance(map_data, str) and map_data:
        # 舊格式：純 ASCII 字串（無座標）
        map_section = map_data

    return (
        f"釣魚卡住狀態：\n"
        f"Bot 位置：x={pos.get('x', '?'):.1f}, z={pos.get('z', '?'):.1f}\n"
        f"目標水面：{water}\n"
        f"背包：{json.dumps(state.get('inventory', []), ensure_ascii=False)}\n\n"
        f"周圍地形（B=Bot, W=水, .=可走, #=阻擋, ~=懸崖）：\n"
        f"{map_section}\n"
    )


async def handle(state: dict, llm: LLMClient) -> dict | None:
    prompt = build_prompt(state)
    response = None
    try:
        print(f"[Skill/fishing] Prompt:\n{prompt}\n---")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        import re
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        # 去掉 markdown code block（```json ... ``` 或 ``` ... ```）
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = json.loads(clean)
        return {"command": "fishing_decision", **decision}
    except Exception as e:
        print(f"[Skill/fishing] LLM 回應解析失敗: {e}\n原始回應: {response!r}")
        return None
