from agent.brain import LLMClient

RAW_FOOD_ITEMS = {
    'beef', 'porkchop', 'chicken', 'mutton', 'rabbit',
    'cod', 'salmon', 'potato',
}
ANIMAL_MOBS = {'cow', 'pig', 'chicken', 'sheep', 'rabbit'}


async def handle(state: dict, llm: LLMClient) -> list | None:
    food = state.get("food", 20)
    inventory = state.get("inventory", [])
    entities = state.get("entities", [])

    inv_map = {i["name"]: i["count"] for i in inventory}

    # 優先 1：背包有生肉/生魚 → 烤熟
    for raw in RAW_FOOD_ITEMS:
        if inv_map.get(raw, 0) > 0:
            print(f"[Skill/food] 背包有 {raw}，烤熟後食用")
            return [
                {"command": "chat", "text": f"背包有 {raw}，先烤熟再吃"},
                {"command": "smelt", "args": [raw]},
            ]

    # 優先 2：附近有動物 → 戰鬥獲取生肉
    nearby_animals = [
        e for e in entities
        if e.get("name") in ANIMAL_MOBS and e.get("distance", 999) <= 20
    ]
    if nearby_animals:
        closest = min(nearby_animals, key=lambda e: e.get("distance", 999))
        print(f"[Skill/food] 附近有 {closest['name']}，去打來取肉")
        return [
            {"command": "chat", "text": f"附近有 {closest['name']}，去打來取肉"},
            {"command": "combat", "goal": {"target": "animal"}},
        ]

    # 優先 3：釣魚
    print(f"[Skill/food] 食物不足（{food}/20），開始釣魚")
    return [
        {"command": "chat", "text": "食物不足，去釣魚補充"},
        {"command": "fish"},
    ]
