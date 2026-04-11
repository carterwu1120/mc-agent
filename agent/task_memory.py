import json
import os
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from agent.plan_utils import build_step_records, normalize_commands

FILE = os.path.join(os.path.dirname(__file__), 'data', 'task.json')

MAX_INTERRUPTED_TASKS = 3
MAX_RECENT_TRANSITIONS = 10
INTERRUPTED_TTL = timedelta(hours=6)

_TASK_KEYS = {
    "id", "goal", "final_goal", "commands", "steps", "currentStep",
    "context", "runtime", "status", "interruptedBy", "createdAt",
}


def save(goal: str, commands: list, final_goal: str | None = None) -> dict:
    commands = normalize_commands(commands)
    prev = _load_raw()
    if final_goal is None and prev:
        final_goal = prev.get("final_goal")
    task = {
        "id": uuid.uuid4().hex[:8],
        "goal": goal,
        "final_goal": final_goal,
        "commands": commands,
        "steps": build_step_records(commands),
        "currentStep": 0,
        "context": {},
        "runtime": {},
        "status": "running",
        "interruptedBy": None,
        "createdAt": _now_iso(),
        "interruptedTasks": list((prev or {}).get("interruptedTasks") or []),
        "recentTransitions": list((prev or {}).get("recentTransitions") or []),
    }
    if prev and prev.get("status") in ("running", "interrupted") and prev.get("goal") != goal:
        _append_transition(
            task,
            from_task=prev,
            to_goal=goal,
            reason="task_replaced",
        )
    _write(task)
    return task


def set_final_goal(final_goal: str) -> None:
    _patch({"final_goal": final_goal})


def load_any() -> dict | None:
    return _load_raw()


def update_step(step: int) -> None:
    _patch({"currentStep": step})


def mark_step_running(step: int) -> None:
    t = _load_raw()
    if t and "steps" in t and step < len(t["steps"]):
        t["steps"][step]["status"] = "running"
        _write(t)


def mark_step_done(step: int) -> None:
    t = _load_raw()
    if t and "steps" in t and step < len(t["steps"]):
        t["steps"][step]["status"] = "done"
        _write(t)


def mark_step_failed(step: int, error: str | None = None) -> None:
    t = _load_raw()
    if t and "steps" in t and step < len(t["steps"]):
        t["steps"][step]["status"] = "failed"
        t["steps"][step]["error"] = error
        _write(t)


def update_context(patch: dict) -> None:
    t = _load_raw()
    if t is None:
        return
    ctx = dict(t.get("context") or {})
    changed = False
    for key, value in (patch or {}).items():
        if ctx.get(key) != value:
            ctx[key] = value
            changed = True
    if not changed:
        return
    t["context"] = ctx
    _write(t)


def update_step_context(step: int, patch: dict) -> None:
    t = _load_raw()
    if t is None or "steps" not in t or step >= len(t["steps"]):
        return
    step_obj = t["steps"][step]
    ctx = dict(step_obj.get("context") or {})
    changed = False
    for key, value in (patch or {}).items():
        if ctx.get(key) != value:
            ctx[key] = value
            changed = True
    if not changed:
        return
    step_obj["context"] = ctx
    _write(t)


def update_runtime(patch: dict) -> None:
    t = _load_raw()
    if t is None:
        return
    runtime = dict(t.get("runtime") or {})
    changed = False
    for key, value in (patch or {}).items():
        if runtime.get(key) != value:
            runtime[key] = value
            changed = True
    if not changed:
        return
    t["runtime"] = runtime
    _write(t)


def replace_remaining_steps(from_step: int, new_commands: list) -> None:
    t = _load_raw()
    if t is None:
        return
    kept = t.get("steps", [])[:from_step]
    previous_command = kept[-1]["cmd"] if kept else None
    new_commands = normalize_commands(new_commands, previous_command=previous_command)
    new_steps = build_step_records(new_commands)
    t["steps"] = kept + new_steps
    t["commands"] = [s["cmd"] for s in t["steps"]]
    _write(t)


def remember_interrupted(
    goal: str,
    commands: list,
    *,
    final_goal: str | None = None,
    context: dict | None = None,
    runtime: dict | None = None,
    reason: str = "player_interrupt",
) -> dict | None:
    commands = normalize_commands(commands)
    if not commands:
        return None

    t = _load_raw() or _normalize_root({})
    created_at = _now_iso()
    summary = {
        "id": uuid.uuid4().hex[:8],
        "goal": goal,
        "final_goal": final_goal if final_goal is not None else t.get("final_goal"),
        "commands": commands,
        "steps": build_step_records(commands),
        "currentStep": 0,
        "context": deepcopy(context or {}),
        "runtime": deepcopy(runtime or {}),
        "status": "interrupted",
        "interruptedBy": reason,
        "createdAt": created_at,
        "interruptedAt": created_at,
    }
    interrupted = list(t.get("interruptedTasks") or [])
    if interrupted and interrupted[0].get("goal") == summary["goal"] and interrupted[0].get("commands") == summary["commands"]:
        interrupted[0] = summary
    else:
        interrupted.insert(0, summary)
    t["interruptedTasks"] = interrupted
    _write(t)
    return summary


def latest_interrupted() -> dict | None:
    t = _load_raw()
    if not t:
        return None
    interrupted = t.get("interruptedTasks") or []
    return interrupted[0] if interrupted else None


def interrupted_tasks() -> list[dict]:
    t = _load_raw()
    if not t:
        return []
    return list(t.get("interruptedTasks") or [])


def update_latest_interrupted_step_context(step: int, patch: dict) -> None:
    t = _load_raw()
    if not t:
        return
    interrupted = list(t.get("interruptedTasks") or [])
    if not interrupted:
        return
    task = interrupted[0]
    steps = task.get("steps") or []
    if step >= len(steps):
        return
    ctx = dict((steps[step] or {}).get("context") or {})
    changed = False
    for key, value in (patch or {}).items():
        if ctx.get(key) != value:
            ctx[key] = value
            changed = True
    if not changed:
        return
    steps[step]["context"] = ctx
    task["steps"] = steps
    interrupted[0] = task
    t["interruptedTasks"] = interrupted
    _write(t)


def interrupt(reason: str) -> None:
    _patch({"status": "interrupted", "interruptedBy": reason})


def done() -> None:
    _patch({"status": "done"})


def failed() -> None:
    _patch({"status": "failed"})


def resume_interrupted(new_commands: list | None = None, goal: str | None = None) -> dict | None:
    t = _load_raw()
    if t is None:
        return None
    current_step = t.get("currentStep", 0)
    if new_commands is not None:
        t = _load_raw() or t
        kept = t.get("steps", [])[:current_step]
        previous_command = kept[-1]["cmd"] if kept else None
        new_commands = normalize_commands(new_commands, previous_command=previous_command)
        new_steps = build_step_records(new_commands)
        t["steps"] = kept + new_steps
        t["commands"] = [s["cmd"] for s in t["steps"]]
    if goal is not None:
        t["goal"] = goal
    t["status"] = "running"
    t["interruptedBy"] = None
    _write(t)
    return t


def load() -> dict | None:
    try:
        t = _load_raw()
        if t is None:
            return None
        if t.get("status") in ("running", "interrupted"):
            return t
        steps = t.get("steps", [])
        has_pending = any(s.get("status") == "pending" for s in steps)
        if has_pending:
            t["status"] = "interrupted"
            _write(t)
            return t
        return None
    except Exception:
        return None


def clear() -> None:
    try:
        os.remove(FILE)
    except Exception:
        pass


def _load_raw() -> dict | None:
    try:
        with open(FILE, 'r', encoding='utf-8') as f:
            return _normalize_root(json.load(f))
    except Exception:
        return None


def _write(data: dict) -> None:
    normalized = _normalize_root(data)
    _prune(normalized)
    with open(FILE, 'w', encoding='utf-8') as f:
        json.dump(normalized, f, indent=2, ensure_ascii=False)


def _patch(patch: dict) -> None:
    t = _load_raw()
    if t is None:
        return
    t.update(patch)
    _write(t)


def _normalize_root(data: dict) -> dict:
    root = dict(data or {})
    root.setdefault("interruptedTasks", [])
    root.setdefault("recentTransitions", [])
    root.setdefault("context", {})
    root.setdefault("runtime", {})
    root.setdefault("commands", [])
    root.setdefault("steps", [])
    root.setdefault("currentStep", 0)
    return root


def _prune(root: dict) -> None:
    interrupted = []
    for task in root.get("interruptedTasks") or []:
        normalized = _normalize_archived_task(task)
        if not normalized:
            continue
        interrupted.append(normalized)
    root["interruptedTasks"] = interrupted[:MAX_INTERRUPTED_TASKS]

    transitions = []
    for item in root.get("recentTransitions") or []:
        normalized = _normalize_transition(item)
        if normalized:
            transitions.append(normalized)
    root["recentTransitions"] = transitions[:MAX_RECENT_TRANSITIONS]


def _normalize_archived_task(task: dict | None) -> dict | None:
    if not isinstance(task, dict):
        return None
    commands = normalize_commands(task.get("commands") or [])
    if not commands:
        return None
    interrupted_at = _parse_iso(task.get("interruptedAt"))
    if interrupted_at and datetime.utcnow() - interrupted_at > INTERRUPTED_TTL:
        return None
    steps = task.get("steps") or build_step_records(commands)
    return {
        "id": task.get("id") or uuid.uuid4().hex[:8],
        "goal": task.get("goal") or "",
        "final_goal": task.get("final_goal"),
        "commands": commands,
        "steps": steps,
        "currentStep": int(task.get("currentStep", 0) or 0),
        "context": dict(task.get("context") or {}),
        "runtime": dict(task.get("runtime") or {}),
        "status": "interrupted",
        "interruptedBy": task.get("interruptedBy"),
        "createdAt": task.get("createdAt") or _now_iso(),
        "interruptedAt": task.get("interruptedAt") or _now_iso(),
    }


def _normalize_transition(item: dict | None) -> dict | None:
    if not isinstance(item, dict):
        return None
    from_goal = item.get("fromGoal") or item.get("from")
    to_goal = item.get("toGoal") or item.get("to")
    if not from_goal and not to_goal:
        return None
    return {
        "fromGoal": from_goal,
        "toGoal": to_goal,
        "reason": item.get("reason"),
        "at": item.get("at") or _now_iso(),
    }


def _append_transition(root: dict, *, from_task: dict | None, to_goal: str | None, reason: str) -> None:
    if not from_task:
        return
    transition = {
        "fromGoal": from_task.get("goal"),
        "toGoal": to_goal,
        "reason": reason,
        "at": _now_iso(),
    }
    recent = list(root.get("recentTransitions") or [])
    recent.insert(0, transition)
    root["recentTransitions"] = recent


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None
