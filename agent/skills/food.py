from agent.brain import LLMClient

RAW_FOOD_ITEMS = {
    'beef', 'porkchop', 'chicken', 'mutton', 'rabbit',
    'cod', 'salmon', 'potato',
}
ANIMAL_MOBS = {'cow', 'pig', 'chicken', 'sheep', 'rabbit'}


async def handle(state: dict, llm: LLMClient) -> list | None:
    food = state.get("food", 20)
    activity = state.get("activity", "idle")
    print(f"[Skill/food] 食物不足（{food}/20），activity={activity}，啟動 getfood 流程")
    return [
        {"command": "chat", "text": f"食物不足（{food}/20），暫停目前任務去補充食物"},
        {"action": "plan", "commands": ["getfood"], "goal": "補充食物", "preserve_task": True},
    ]
