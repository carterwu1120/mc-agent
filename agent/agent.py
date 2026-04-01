import asyncio
import json
import re
from collections import deque
import websockets
from dotenv import load_dotenv

from agent.brain import LLMClient, GeminiClient, OllamaClient
from agent.logger import init_logger
from agent.skills import inventory as inventory_skill
from agent.skills import craft_decision as craft_decision_skill
from agent.skills import activity_stuck as activity_stuck_skill
from agent.skills import food as food_skill
from agent.skills import planner as planner_skill
from agent.skills import self_task as self_task_skill
from agent.skills import task_arbitration as task_arbitration_skill
from agent.executor import PlanExecutor
from agent import task_memory

load_dotenv()
init_logger("brain")

WS_URL = "ws://localhost:3001"

# ── 在這裡切換 LLM ────────────────────────────────────────
llm: LLMClient = GeminiClient()
# llm = OllamaClient(model="qwen3:14b")

executor = PlanExecutor()

async def _on_done(_state: dict, _llm: LLMClient):
    executor.signal_done(_state)
    return None

# ── 各事件對應的 skill handler ────────────────────────────
HANDLERS = {
    "inventory_full": inventory_skill.handle,
    "craft_decision": craft_decision_skill.handle,
    "activity_stuck": activity_stuck_skill.handle,
    "food_low":       food_skill.handle,
    "tick":           self_task_skill.handle,
    "action_done":    _on_done,
    "activity_done":  _on_done,
    "chat":           planner_skill.handle,
}

_thinking: set[str] = set()  # 正在處理中的事件 type，防止重複 call LLM
_last_self_task_at = 0.0
SELF_TASK_COOLDOWN = 60.0
_idle_started_at: float | None = None
_queued_player_tasks: deque[str] = deque()
_latest_state: dict = {}


def _save_current_task_to_memory(state: dict) -> None:
    """把目前 activity stack 的頂層任務存進 task_memory，供之後 resume。"""
    stack = state.get("stack", [])
    if not stack:
        return
    top = stack[-1]
    activity = top.get("activity", "")
    goal = top.get("goal", {})
    progress = top.get("progress", {})

    resume_cmd_map = {
        "fishing":  lambda g, p: f"fish catches {max(1, g.get('catches',0) - p.get('catches',0))}",
        "chopping": lambda g, p: f"chop logs {max(1, g.get('logs',0) - p.get('logs',0))}",
        "mining":   lambda g, p: f"mine {g.get('target','iron')} {max(1, g.get('count',0) - p.get('count',0))}",
        "smelting": lambda g, p: f"smelt {g.get('target','iron')} {max(1, g.get('count',0) - p.get('smelted',0))}",
        "hunting":  lambda g, p: f"hunt count {max(1, g.get('count',0) - p.get('count',0))}",
        "getfood":  lambda g, p: f"getfood count {max(1, g.get('count',0) - p.get('count',0))}",
    }

    resume_fn = resume_cmd_map.get(activity)
    if not resume_fn:
        return  # combat / surface / explore 等無法意義恢復，不存

    try:
        resume_cmd = resume_fn(goal, progress)
    except Exception:
        return

    goal_str = f"{activity} {goal}"
    task_memory.interrupt("player_interrupt")
    task_memory.save(goal_str, [resume_cmd])
    task_memory.interrupt("player_interrupt")  # save 會重設 status，再標記一次
    print(f"[TaskMem] 儲存任務: {goal_str} → [{resume_cmd}]")


def _augment_state(state: dict, player_task: str | None = None) -> dict:
    copied = dict(state)
    copied["queued_tasks"] = list(_queued_player_tasks)
    copied["player_task"] = player_task
    return copied


def _stop_command_for_activity(activity: str | None) -> dict | None:
    mapping = {
        "fishing": {"command": "stopfish"},
        "chopping": {"command": "stopchop"},
        "mining": {"command": "stopmine"},
        "smelting": {"command": "stopsmelt"},
        "surface": {"command": "stopsurface"},
        "explore": {"command": "stopexplore"},
        "combat": {"command": "stopcombat"},
        "hunting": {"command": "stophunt"},
        "getfood": {"command": "stopgetfood"},
    }
    return mapping.get(activity or "")


def _is_system_chat_message(message: str) -> bool:
    if not message:
        return False
    lowered = message.strip().lower()
    if re.match(r"^teleported\s+.+\s+to\s+agent]?$", lowered):
        return True
    if re.match(r"^gave\s+.+\s+to\s+agent]?$", lowered):
        return True
    if re.match(r"^set\s+the\s+time\s+to\s+\d+]?$", lowered):
        return True
    return False


def _distance_sq(a: dict | None, b: dict | None) -> float:
    ax = float((a or {}).get("x", 0.0))
    ay = float((a or {}).get("y", 0.0))
    az = float((a or {}).get("z", 0.0))
    bx = float((b or {}).get("x", 0.0))
    by = float((b or {}).get("y", 0.0))
    bz = float((b or {}).get("z", 0.0))
    return (ax - bx) ** 2 + (ay - by) ** 2 + (az - bz) ** 2


def _is_stale_response(event_type: str, request_state: dict) -> bool:
    if not _latest_state:
        return False

    current_activity = _latest_state.get("activity", "idle")
    request_activity = request_state.get("activity", "idle")
    current_pos = _latest_state.get("pos") or {}
    request_pos = request_state.get("pos") or {}
    moved_far = _distance_sq(current_pos, request_pos) > 12 ** 2

    if event_type == "craft_decision":
        return current_activity != request_activity or moved_far

    if event_type == "activity_stuck":
        same_activity = current_activity == request_activity
        same_reason = _latest_state.get("detail") == request_state.get("detail")
        return (not same_activity) or (not same_reason)

    if event_type == "tick":
        return current_activity != request_activity

    return False


async def _handle_and_send(state: dict, handler, ws) -> None:
    event_type = state.get("type")
    global _last_self_task_at
    global _idle_started_at
    _thinking.add(event_type)
    try:
        if event_type == "tick":
            now = asyncio.get_running_loop().time()
            if executor.is_running():
                return
            if state.get("activity") != "idle":
                _idle_started_at = None
                return
            if _idle_started_at is None:
                _idle_started_at = now
                return
            if now - _idle_started_at < SELF_TASK_COOLDOWN:
                return
            if now - _last_self_task_at < SELF_TASK_COOLDOWN:
                return
            _last_self_task_at = now
        print(f"[Agent] 呼叫 LLM 處理 {event_type}...")
        result = await handler(state, llm)
        if not result:
            return
        if _is_stale_response(event_type, state):
            print(f"[Agent] 忽略過期的 {event_type} 回應")
            return
        # Plan response: execute commands sequentially
        if isinstance(result, dict) and result.get('action') == 'plan':
            commands = result.get('commands', [])
            goal = result.get('goal', '')
            if commands:
                if executor.is_running():
                    print('[Agent] 計畫執行中，中止舊計畫')
                    executor.abort()
                    # 只有不含自動恢復的事件才標記 interrupted（玩家打斷由 _handle_player_chat 處理）
                asyncio.create_task(executor.execute(commands, ws, goal=goal))
            return
        # Standard response: send immediately
        actions = result if isinstance(result, list) else [result]
        for a in actions:
            print(f"[Agent] 送出決策: {a}")
            await ws.send(json.dumps(a))
    except Exception as e:
        print(f"[Agent] {event_type} 處理失敗: {e}")
    finally:
        _thinking.discard(event_type)


async def _handle_player_chat(state: dict, ws) -> None:
    message = state.get("message", "")
    if _is_system_chat_message(message):
        print(f"[TaskArb] 忽略系統聊天: {message}")
        return

    activity = state.get("activity", "idle")
    busy = activity != "idle" or executor.is_running()

    if busy:
        arb_state = _augment_state(state, player_task=state.get("message"))
        decision = await task_arbitration_skill.handle(arb_state, llm)
        if decision:
            text = decision.get("text", "").strip()
            choice = decision.get("decision")
            if choice == "queue":
                _queued_player_tasks.append(state.get("message", ""))
                print(f"[TaskArb] 玩家任務已排隊: {state.get('message')}")
                return
            if choice == "defer":
                if text:
                    await ws.send(json.dumps({"command": "chat", "text": text}))
                print(f"[TaskArb] 暫緩玩家任務: {state.get('message')}")
                return
            if choice == "interrupt":
                if text:
                    await ws.send(json.dumps({"command": "chat", "text": text}))
                if executor.is_running():
                    print("[TaskArb] 中止目前計畫，改執行玩家任務")
                    executor.abort()
                # 儲存目前任務（不管 executor 是否在跑）
                _save_current_task_to_memory(state)
                stop_cmd = _stop_command_for_activity(activity)
                if stop_cmd:
                    await ws.send(json.dumps(stop_cmd))
                state = {**state, "activity": "idle", "stack": []}

    planner_state = _augment_state(state, player_task=state.get("message"))
    await _handle_and_send(planner_state, planner_skill.handle, ws)


async def run():
    while True:
        try:
            print(f"[Agent] 連線到 {WS_URL} ...")
            async with websockets.connect(WS_URL) as ws:
                print("[Agent] 已連線！等待 state...")
                async for raw in ws:
                    state = json.loads(raw)
                    _latest_state.clear()
                    _latest_state.update(state)
                    event_type = state.get("type")
                    pos = state.get("pos") or {}
                    print(f"[State] type={event_type}  "
                          f"pos=({pos.get('x', 0):.1f}, {pos.get('y', 0):.1f}, {pos.get('z', 0):.1f})  "
                          f"hp={state.get('health')}  food={state.get('food')}")

                    if (
                        event_type == "tick"
                        and state.get("activity") == "idle"
                        and not executor.is_running()
                        and _queued_player_tasks
                        and "chat" not in _thinking
                    ):
                        queued_message = _queued_player_tasks.popleft()
                        print(f"[TaskArb] 取出排隊玩家任務: {queued_message}")
                        queued_state = _augment_state({**state, "type": "chat", "message": queued_message}, player_task=queued_message)
                        asyncio.create_task(_handle_and_send(queued_state, planner_skill.handle, ws))
                        continue

                    handler = HANDLERS.get(event_type)
                    if not handler:
                        continue
                    if event_type in _thinking:
                        print(f"[Agent] {event_type} LLM 仍在處理中，跳過")
                        continue

                    if event_type == "chat":
                        asyncio.create_task(_handle_player_chat(state, ws))
                        continue

                    asyncio.create_task(_handle_and_send(state, handler, ws))
        except Exception as e:
            print(f"[Agent] 連線中斷: {e}，3 秒後重連...")
            _thinking.clear()
            await asyncio.sleep(3)


if __name__ == "__main__":
    asyncio.run(run())
