"""One strategy decision per attempt; supervisor owns retries and deadlines."""
import json
from agent.skills.command_validation import PLAN_ALLOWED_COMMANDS, validate_commands
from agent.skills.commands_ref import command_list
from agent.skills.state_summary import summary_json
from agent.skills.stuck.llm_utils import parse_json_with_repair
from agent.plan_utils import normalize_commands

# These commands have completion semantics in PlanExecutor. Pure chat/idle and
# stack resume are not recovery plans: old activities have already been stopped.
ALLOWED = PLAN_ALLOWED_COMMANDS - {"resumetask", "come", "look"}
SYSTEM = """你是 Minecraft 任務恢復規劃者。舊動作已中斷，舊 stack 不會自動恢復。
依現況選擇可執行的短期策略，保留原任務目標與必要的剩餘步驟，扣除已取得的數量。
腳本不能做到的事要坦白說明；不可只重送已失敗策略或跳過必要前置條件假裝完成。
只能輸出 JSON，以下二選一：
{"action":"replan","commands":["..."],"expected_outcome":"可觀察的結果，例如新增 8 個圓石","text":"策略理由"}
{"action":"wait","text":"明確的阻礙，以及需要玩家提供什麼"}
最多 8 個指令。expected_outcome 是給下一次觀測與決策的說明，完成仍由執行器驗證。
可用指令：
""" + command_list(sorted(ALLOWED))


async def handle(state, llm):
    context = {k: state.get(k) for k in ("reason", "detail", "goal", "progress", "plan_context", "stack",
               "navigation", "progress_evidence", "recovery_history", "decision_error") if state.get(k) is not None}
    response = await llm.chat([{"role": "user", "content": summary_json(state) + "\n" + json.dumps(context, ensure_ascii=False)}], system=SYSTEM)
    result = parse_json_with_repair(response)
    if not isinstance(result, dict):
        raise ValueError("decision must be an object")
    if not isinstance(result.get("text"), str) or not result["text"].strip():
        raise ValueError("decision needs a reason")
    if result.get("action") == "wait":
        return result
    commands = result.get("commands")
    if result.get("action") != "replan" or not isinstance(commands, list) or not 1 <= len(commands) <= 8:
        raise ValueError("return replan with 1-8 commands, or wait with a reason")
    errors = validate_commands(commands, allowed_commands=ALLOWED)
    if errors:
        raise ValueError(str(errors))
    done = (state.get('plan_context') or {}).get('done_steps') or []
    commands = normalize_commands(commands, previous_command=done[-1] if done else None)
    if not commands:
        raise ValueError('replan has no executable steps after normalization')
    result['commands'] = commands
    if not isinstance(result.get("expected_outcome"), str) or not result["expected_outcome"].strip():
        raise ValueError("replan needs an observable expected_outcome")
    return result
