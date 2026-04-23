import json
import os
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from agent.plan_utils import build_step_records, normalize_commands
from agent import history_db

_DATA_DIR = os.environ.get('BOT_DATA_DIR', os.path.join(os.path.dirname(__file__), 'data'))
os.makedirs(_DATA_DIR, exist_ok=True)
FILE = os.path.join(_DATA_DIR, 'task.json')

MAX_INTERRUPTED_TASKS = 3
MAX_RECENT_TRANSITIONS = 10
MAX_RECENT_EVENTS = 20
MAX_RECENT_FAILURES = 8
INTERRUPTED_TTL = timedelta(hours=6)
EVENT_TTL = timedelta(hours=6)
FAILURE_TTL = timedelta(hours=6)

_TASK_KEYS = {
    "id", "goal", "final_goal", "commands", "steps", "currentStep",
    "context", "runtime", "status", "interruptedBy", "createdAt", "source",
}


def save(goal: str, commands: list, final_goal: str | None = None, source: str | None = None) -> dict:
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
        "source": source or "unknown",
        "interruptedTasks": list((prev or {}).get("interruptedTasks") or []),
        "recentTransitions": list((prev or {}).get("recentTransitions") or []),
        "recentEvents": list((prev or {}).get("recentEvents") or []),
        "recentFailures": list((prev or {}).get("recentFailures") or []),
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
        cmd = (t["steps"][step] or {}).get("cmd")
        expected_activity = _command_to_activity(cmd)
        _append_failure(
            t,
            goal=t.get("goal"),
            cmd=cmd,
            step=step,
            reason=error or "failed",
            activity=expected_activity,
        )
        _write(t)
        failures = t.get("recentFailures") or []
        if failures:
            history_db._fire_and_forget(history_db.write_failure, dict(failures[0]), t.get("id"))


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
    t = _load_raw()
    if t is None:
        return
    t["status"] = "interrupted"
    t["interruptedBy"] = reason
    steps = t.get("steps") or []
    current_step = int(t.get("currentStep", 0) or 0)
    command = steps[current_step].get("cmd") if 0 <= current_step < len(steps) else None
    _append_event(
        t,
        event_type=_event_type_for_interrupt_reason(reason),
        reason=reason,
        from_task=t,
        to_goal=t.get("goal"),
        command=command,
        step=current_step,
    )
    _write(t)
    history_db._fire_and_forget(history_db.archive_task, dict(t))


def done() -> None:
    _patch({"status": "done"})
    t = _load_raw()
    if t:
        history_db._fire_and_forget(history_db.archive_task, dict(t))


def failed() -> None:
    _patch({"status": "failed"})
    t = _load_raw()
    if t:
        history_db._fire_and_forget(history_db.archive_task, dict(t))


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
    _append_event(
        t,
        event_type="resumetask",
        reason="resume_interrupted",
        from_task=t,
        to_goal=t.get("goal"),
        command=(t.get("steps") or [{}])[current_step].get("cmd") if (t.get("steps") or []) and current_step < len(t.get("steps") or []) else None,
        step=current_step,
    )
    _write(t)
    return t


def record_event(
    event_type: str,
    *,
    reason: str | None = None,
    details: dict | None = None,
    to_goal: str | None = None,
    command: str | None = None,
    step: int | None = None,
) -> None:
    t = _load_raw() or _normalize_root({})
    _append_event(
        t,
        event_type=event_type,
        reason=reason,
        details=details,
        from_task=t if t.get("goal") else None,
        to_goal=to_goal,
        command=command,
        step=step,
    )
    _write(t)
    events = t.get("recentEvents") or []
    if events:
        history_db._fire_and_forget(history_db.write_event, dict(events[0]), t.get("id"))


def recent_events() -> list[dict]:
    t = _load_raw()
    if not t:
        return []
    return list(t.get("recentEvents") or [])


def record_failure(
    *,
    reason: str,
    cmd: str | None = None,
    step: int | None = None,
    goal: str | None = None,
    activity: str | None = None,
) -> None:
    t = _load_raw() or _normalize_root({})
    _append_failure(
        t,
        goal=goal or t.get("goal"),
        cmd=cmd,
        step=step,
        reason=reason,
        activity=activity or _command_to_activity(cmd),
    )
    _write(t)
    failures = t.get("recentFailures") or []
    if failures:
        history_db._fire_and_forget(history_db.write_failure, dict(failures[0]), t.get("id"))


def recent_failures() -> list[dict]:
    t = _load_raw()
    if not t:
        return []
    return list(t.get("recentFailures") or [])


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
    root.setdefault("recentEvents", [])
    root.setdefault("recentFailures", [])
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

    events = []
    for item in root.get("recentEvents") or []:
        normalized = _normalize_event(item)
        if normalized:
            events.append(normalized)
    root["recentEvents"] = events[:MAX_RECENT_EVENTS]

    failures = []
    for item in root.get("recentFailures") or []:
        normalized = _normalize_failure(item)
        if normalized:
            failures.append(normalized)
    root["recentFailures"] = failures[:MAX_RECENT_FAILURES]


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


def _append_event(
    root: dict,
    *,
    event_type: str,
    reason: str | None = None,
    details: dict | None = None,
    from_task: dict | None = None,
    to_goal: str | None = None,
    command: str | None = None,
    step: int | None = None,
) -> None:
    event = {
        "type": event_type,
        "goal": (from_task or {}).get("goal"),
        "toGoal": to_goal,
        "reason": reason,
        "command": command,
        "step": step,
        "details": dict(details or {}),
        "at": _now_iso(),
    }
    recent = list(root.get("recentEvents") or [])
    recent.insert(0, event)
    root["recentEvents"] = recent


def _append_failure(
    root: dict,
    *,
    goal: str | None,
    cmd: str | None,
    step: int | None,
    reason: str,
    activity: str | None,
) -> None:
    failure = {
        "goal": goal,
        "command": cmd,
        "step": step,
        "reason": reason,
        "activity": activity,
        "at": _now_iso(),
    }
    recent = list(root.get("recentFailures") or [])
    recent.insert(0, failure)
    root["recentFailures"] = recent


def _normalize_event(item: dict | None) -> dict | None:
    if not isinstance(item, dict):
        return None
    at = _parse_iso(item.get("at"))
    if at and datetime.utcnow() - at > EVENT_TTL:
        return None
    event_type = item.get("type")
    if not event_type:
        return None
    return {
        "type": event_type,
        "goal": item.get("goal"),
        "toGoal": item.get("toGoal"),
        "reason": item.get("reason"),
        "command": item.get("command"),
        "step": item.get("step"),
        "details": dict(item.get("details") or {}),
        "at": item.get("at") or _now_iso(),
    }


def _normalize_failure(item: dict | None) -> dict | None:
    if not isinstance(item, dict):
        return None
    at = _parse_iso(item.get("at"))
    if at and datetime.utcnow() - at > FAILURE_TTL:
        return None
    if not item.get("reason"):
        return None
    return {
        "goal": item.get("goal"),
        "command": item.get("command"),
        "step": item.get("step"),
        "reason": item.get("reason"),
        "activity": item.get("activity"),
        "at": item.get("at") or _now_iso(),
    }


def _command_to_activity(cmd: str | None) -> str | None:
    verb = (cmd or "").split()[0] if cmd else ""
    return {
        "mine": "mining",
        "chop": "chopping",
        "fish": "fishing",
        "smelt": "smelting",
        "hunt": "hunting",
        "getfood": "getfood",
        "equip": "equip",
        "deposit": "deposit",
        "explore": "explore",
        "surface": "surface",
    }.get(verb, verb or None)


def _event_type_for_interrupt_reason(reason: str | None) -> str:
    lowered = (reason or "").lower()
    if "abort" in lowered:
        return "abort"
    if "resume" in lowered:
        return "resumetask"
    if "interrupt" in lowered:
        return "interrupt"
    if "skip" in lowered:
        return "skip"
    return "interrupt"


def _now_iso() -> str:
    return datetime.utcnow().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None
