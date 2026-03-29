from agent.brain import LLMClient

RAW_FOOD_ITEMS = {
    'beef', 'porkchop', 'chicken', 'mutton', 'rabbit',
    'cod', 'salmon', 'potato',
}
ANIMAL_MOBS = {'cow', 'pig', 'chicken', 'sheep', 'rabbit'}


async def handle(state: dict, llm: LLMClient) -> list | None:
    food = state.get("food", 20)
    print(f"[Skill/food] 食物不足（{food}/20），啟動 getfood 流程")
    return [
        {"command": "chat", "text": "食物不足，開始蒐集並烹飪食物"},
        {"command": "getfood"},
    ]
