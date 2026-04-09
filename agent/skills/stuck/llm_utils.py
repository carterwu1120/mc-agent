import json
import re

from agent.brain import LLMClient
from agent.skills.command_validation import (
    PLAN_ALLOWED_COMMANDS,
    build_reprompt_suffix,
    validate_commands,
)


def extract_first_json_object(text: str) -> dict:
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


def parse_json_with_repair(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    try:
        return extract_first_json_object(text)
    except json.JSONDecodeError:
        pass

    stripped = text.strip()
    if stripped.startswith("{"):
        open_braces = stripped.count("{")
        close_braces = stripped.count("}")
        if open_braces > close_braces:
            repaired = stripped + ("}" * (open_braces - close_braces))
            try:
                return json.loads(repaired)
            except json.JSONDecodeError:
                pass
            try:
                return extract_first_json_object(repaired)
            except json.JSONDecodeError:
                pass

    command_match = re.search(r'"command"\s*:\s*"([^"]+)"', text)
    if command_match:
        salvaged = {"command": command_match.group(1)}

        text_key = text.find('"text"')
        if text_key != -1:
            first_quote = text.find('"', text_key + len('"text"'))
            if first_quote != -1:
                second_quote = text.find('"', first_quote + 1)
                if second_quote != -1:
                    text_start = second_quote + 1
                    text_end = text.find('"', text_start)
                    if text_end != -1:
                        salvaged["text"] = text[text_start:text_end]

        args_match = re.search(r'"args"\s*:\s*\[\s*"([^"]+)"(?:\s*,\s*"([^"]+)")?', text)
        if args_match:
            salvaged["args"] = [v for v in args_match.groups() if v is not None]

        action_match = re.search(r'"action"\s*:\s*"([^"]+)"', text)
        if action_match:
            salvaged["action"] = action_match.group(1)

        x_match = re.search(r'"x"\s*:\s*(-?\d+(?:\.\d+)?)', text)
        if x_match:
            salvaged["x"] = float(x_match.group(1))

        z_match = re.search(r'"z"\s*:\s*(-?\d+(?:\.\d+)?)', text)
        if z_match:
            salvaged["z"] = float(z_match.group(1))

        logs_match = re.search(r'"logs"\s*:\s*(\d+)', text)
        if logs_match:
            salvaged["goal"] = {"logs": int(logs_match.group(1))}

        if salvaged.get("command"):
            return salvaged

    raise json.JSONDecodeError("No valid JSON object found", text, 0)


async def reprompt_invalid_replan(
    llm: LLMClient,
    prompt: str,
    system: str,
    invalid_commands: list[str],
    errors,
) -> dict | None:
    reprompt = prompt + build_reprompt_suffix(
        invalid_commands=invalid_commands,
        errors=errors,
        allowed_command_keys=PLAN_ALLOWED_COMMANDS,
    )
    try:
        print(f"[Skill/activity_stuck] 偵測到非法 replan，重問一次 LLM：{invalid_commands}")
        response = await llm.chat(
            [{"role": "user", "content": reprompt}],
            system=system,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = parse_json_with_repair(clean)
        if decision.get("action") != "replan":
            return None
        repair_errors = validate_commands(
            decision.get("commands", []),
            allowed_commands=PLAN_ALLOWED_COMMANDS,
        )
        if repair_errors:
            print(f"[Skill/activity_stuck] 修正後 replan 仍不合法：{[e.command for e in repair_errors]}")
            return None
        return decision
    except Exception as e:
        print(f"[Skill/activity_stuck] 修正 replan 失敗: {e}")
        return None


async def reprompt_for_replan_strategy(
    llm: LLMClient,
    prompt: str,
    system: str,
    decision: dict,
    pending_steps: list[str],
) -> dict | None:
    reprompt = (
        prompt
        + "\n\n你上一個回覆用了單一步驟修復，但目前存在未完成的多步驟計畫。"
        + " 這種情況不能只回單一指令，必須在理解目前 activity、卡住原因、剩餘步驟與狀態後，"
        + " 回覆完整剩餘計畫的 replan，或明確回 skip。\n"
        + f"你上一個回覆是：{json.dumps(decision, ensure_ascii=False)}\n"
        + f"目前原計畫剩餘步驟：{pending_steps}\n"
        + "請只回覆以下其中一種 JSON：\n"
        + '{"action":"replan","commands":["...完整剩餘步驟..."],"text":"...理由..."}\n'
        + '{"action":"skip","text":"...理由..."}\n'
        + "不要回單一步驟 command。"
    )
    try:
        print("[Skill/activity_stuck] 需要完整 replan，重問一次 LLM")
        response = await llm.chat(
            [{"role": "user", "content": reprompt}],
            system=system,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        repaired = parse_json_with_repair(clean)
        if repaired.get("action") == "replan":
            repair_errors = validate_commands(
                repaired.get("commands", []),
                allowed_commands=PLAN_ALLOWED_COMMANDS,
            )
            if repair_errors:
                print(f"[Skill/activity_stuck] 完整 replan 仍不合法：{[e.command for e in repair_errors]}")
                return None
            return repaired
        if repaired.get("action") == "skip":
            return repaired
        return None
    except Exception as e:
        print(f"[Skill/activity_stuck] 重問完整 replan 失敗: {e}")
        return None


def replan_fallback(text: str) -> list[dict]:
    return [
        {"command": "chat", "text": text},
        {"action": "skip"},
    ]
