import json
import re
from agent import task_memory
from agent.brain import LLMClient
from agent.brain.cli_agent import CLIProviderError
from agent.context_builder import build_for_skill
from agent.handlers.llm_response import parse_llm_json
from agent.handlers.state_summary import summary_json
from agent.handlers.prompts import render_prompt

SYSTEM_PROMPT = render_prompt("task_arbitration")

ALLOWED_DECISIONS = {"interrupt", "queue", "defer", "update", "resume", "reply"}


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
    task_history = build_for_skill(
        "task_arbitration",
        task_memory.recent_events(),
        task_memory.recent_failures(),
    )
    prompt = (
        f"玩家訊息（不一定是任務）：{message}\n\n"
        f"目前任務：{json.dumps(state.get('chat_task'), ensure_ascii=False)}\n"
        f"請先分辨狀態更新、恢復請求、詢問或新任務，再選擇 decision。\n\n"
        f"{summary_json(state)}"
        f"{task_history}"
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
    except CLIProviderError:
        # The model never ran; the caller must not report this as a misunderstanding.
        raise
    except Exception as e:
        print(f"[TaskArb] 解析失敗: {e}\n原始回應: {response!r}")
        return None
