import json
import re
from agent.brain import LLMClient
from agent.skills.llm_response import parse_llm_json
from agent.skills.state_summary import summary_json

SYSTEM_PROMPT = """你是 Minecraft 陪玩型 agent 的玩家任務仲裁助手。
當玩家插入新任務時，你要判斷：
1. interrupt：立即中斷目前任務，先做玩家任務
2. queue：先把玩家任務排隊，等目前任務完成再做
3. defer：暫時拒絕或延後，因為目前狀態不適合

只能回覆 JSON（不要加其他文字）：
{"decision":"interrupt","text":"..."}
{"decision":"queue","text":"..."}
{"decision":"defer","text":"..."}

判斷原則：
- 玩家任務通常優先於自主任務
- 若目前在危險狀態、戰鬥、逃生、低血低飢餓，優先 defer
- 若目前任務很短且快完成，可 queue
- 若目前只是一般自主任務，通常 interrupt
- text 必須是繁體中文一句話
"""

ALLOWED_DECISIONS = {"interrupt", "queue", "defer"}


def _extract_first_json_object(text: str) -> dict:
    decoder = json.JSONDecoder()
    idx = text.find("{")
    while idx != -1:
        try:
            obj, _end = decoder.raw_decode(text[idx:])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        idx = text.find("{", idx + 1)
    raise json.JSONDecodeError("No valid JSON object found", text, 0)


async def handle(state: dict, llm: LLMClient) -> dict | None:
    message = state.get("message", "")
    prompt = (
        f"玩家剛剛提出新任務／新要求：{message}\n\n"
        f"請根據以下狀態摘要，判斷要 interrupt、queue 還是 defer。\n\n"
        f"{summary_json(state)}"
    )

    response = None
    try:
        print(f"[TaskArb] 評估玩家任務: {message}")
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        try:
            raw = json.loads(clean)
        except json.JSONDecodeError:
            raw = _extract_first_json_object(clean)
        decision = parse_llm_json(raw, "TaskArb")

        if decision.get("decision") not in ALLOWED_DECISIONS:
            print(f"[TaskArb] 無效 decision，忽略: {decision}")
            return None
        if not isinstance(decision.get("text", ""), str):
            print(f"[TaskArb] decision 缺少 text，忽略: {decision}")
            return None
        return decision
    except Exception as e:
        print(f"[TaskArb] 解析失敗: {e}\n原始回應: {response!r}")
        return None
