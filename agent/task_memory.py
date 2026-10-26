"""The current task, persisted to BOT_DATA_DIR/task.json.

Single-task mode: the file holds only the task being worked on. History
(events, failures, finished tasks) goes to the database, not this file.

    id, goal, final_goal?, status, interruptedBy?, source, createdAt,
    currentStep, steps[{cmd, status, error?}],
    context{workPos?, objective?}      -- where the work is and what it must produce
    runtime{supervision?, food_wait_*?} -- live coordination state, overwritten
    goalVerified?, goal_count?, actual_count?  -- set when done
"""
import json
import os
import uuid
from datetime import datetime
from agent.plan_utils import build_step_records, normalize_commands
from agent import history_db

_DATA_DIR = os.environ.get('BOT_DATA_DIR', os.path.join(os.path.dirname(__file__), 'data'))
os.makedirs(_DATA_DIR, exist_ok=True)
FILE = os.path.join(_DATA_DIR, 'task.json')

# Written in this order so the file reads top-down; anything else goes after.
_KEY_ORDER = (
    "id", "goal", "final_goal", "status", "interruptedBy", "source", "createdAt",
    "currentStep", "steps", "context", "runtime", "goalVerified", "goal_count", "actual_count",
)
# Keys from older files that nothing reads any more.
_DROPPED_KEYS = (
    "reason", "commands", "interruptedTasks", "recentTransitions", "recentEvents", "recentFailures",
)
# Older files also copied the live activity into runtime; live_state.json has it.
_DROPPED_RUNTIME_KEYS = (
    "activeActivity", "activeCommand", "activityStack", "activeGoal", "activeProgress",
    "activeWorkPos", "currentPos",
)


def save(
    goal: str,
    commands: list,
    final_goal: str | None = None,
    source: str | None = None,
    task_id: str | None = None,
) -> dict:
    task = {
        "id": task_id or uuid.uuid4().hex[:8],
        "goal": goal,
        "final_goal": final_goal,
        "status": "running",
        "source": source or "unknown",
        "createdAt": _now_iso(),
        "currentStep": 0,
        "steps": build_step_records(normalize_commands(commands)),
        "context": {},
        "runtime": {},
    }
    _write(task)
    return task


def commands(task: dict | None) -> list[str]:
    """The task's command list; it is stored only as steps."""
    return [step["cmd"] for step in (task or {}).get("steps") or []]


def set_final_goal(final_goal: str) -> None:
    _patch({"final_goal": final_goal})


def load_any() -> dict | None:
    return _load_raw()


def update_step(step: int) -> None:
    _patch({"currentStep": step})


def mark_step_running(step: int) -> None:
    _set_step_status(step, "running")


def mark_step_done(step: int) -> None:
    _set_step_status(step, "done")


def mark_step_failed(step: int, error: str | None = None) -> None:
    t = _set_step_status(step, "failed", error=error)
    if t:
        cmd = t["steps"][step].get("cmd")
        history_db.write_failure(_failure(t.get("goal"), cmd, step, error or "failed", _command_to_activity(cmd)), t.get("id"))


def update_context(patch: dict) -> None:
    _merge("context", patch)


def update_runtime(patch: dict) -> None:
    _merge("runtime", patch)


def replace_remaining_steps(from_step: int, new_commands: list) -> None:
    t = _load_raw()
    if t is None:
        return
    t["steps"] = _replace_steps(t, from_step, new_commands)
    _write(t)


def latest_interrupted() -> dict | None:
    return None  # Single-task mode keeps no interrupted-task archive.


def interrupted_tasks() -> list[dict]:
    return []


def interrupt(reason: str) -> None:
    t = _load_raw()
    if t is None:
        return
    t["status"] = "interrupted"
    t["interruptedBy"] = reason
    for step in t["steps"]:
        if step.get("status") == "running":
            step["status"] = "interrupted"
    current_step = int(t.get("currentStep", 0) or 0)
    _write(t)
    history_db.write_event(_event(_event_type_for_interrupt_reason(reason), t, reason, current_step), t.get("id"))
    history_db.archive_task(_for_archive(t))


def done(goal_verified: bool = True, goal_count: int | None = None, actual_count: int | None = None) -> None:
    t = _load_raw()
    if t:
        t["status"] = "done"
        t["goalVerified"] = goal_verified
        t["goal_count"] = goal_count
        t["actual_count"] = actual_count
        _write(t)
        history_db.archive_task(_for_archive(t))


def failed() -> None:
    t = _load_raw()
    if t:
        t["status"] = "failed"
        _write(t)
        history_db.archive_task(_for_archive(t))


def resume_interrupted(new_commands: list | None = None, goal: str | None = None) -> dict | None:
    t = _load_raw()
    if t is None:
        return None
    current_step = int(t.get("currentStep", 0) or 0)
    if new_commands is not None:
        t["steps"] = _replace_steps(t, current_step, new_commands)
    if goal is not None:
        t["goal"] = goal
    t["status"] = "running"
    t["interruptedBy"] = None
    _write(t)
    history_db.write_event(_event("resumetask", t, "resume_interrupted", current_step), t.get("id"))
    return t


def record_event(
    event_type: str,
    *,
    reason: str | None = None,
    details: dict | None = None,
    to_goal: str | None = None,
    command: str | None = None,
    step: int | None = None,
    db_only: bool = False,
) -> None:
    t = _load_raw() or {}
    history_db.write_event({"type": event_type, "goal": t.get("goal"),
        "toGoal": to_goal, "reason": reason, "command": command, "step": step,
        "details": dict(details or {}), "at": _now_iso()}, t.get("id"))


def recent_events() -> list[dict]:
    return []  # Events go to the database only; prompts get no stored history.


def record_failure(
    *,
    reason: str,
    cmd: str | None = None,
    step: int | None = None,
    goal: str | None = None,
    activity: str | None = None,
) -> None:
    t = _load_raw() or {}
    history_db.write_failure(
        _failure(goal or t.get("goal"), cmd, step, reason, activity or _command_to_activity(cmd)), t.get("id"))


def recent_failures() -> list[dict]:
    return []  # Failures go to the database only.


def load() -> dict | None:
    t = _load_raw()
    return t if t and t.get('status') in {'running', 'interrupted'} else None


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
    root = _normalize_root(data)
    ordered = {key: root[key] for key in _KEY_ORDER if key in root}
    ordered.update((key, value) for key, value in root.items() if key not in ordered)
    # Unset optional fields (final_goal, interruptedBy, counts) are left out, not written as null.
    ordered = {key: value for key, value in ordered.items() if value is not None}
    temporary = FILE + ".tmp"
    with open(temporary, 'w', encoding='utf-8') as f:
        json.dump(ordered, f, indent=2, ensure_ascii=False)
    os.replace(temporary, FILE)


def _patch(patch: dict) -> None:
    t = _load_raw()
    if t is None:
        return
    t.update(patch)
    _write(t)


def _merge(key: str, patch: dict) -> None:
    t = _load_raw()
    if t is None:
        return
    merged = {**t[key], **(patch or {})}
    if merged != t[key]:
        t[key] = merged
        _write(t)


def _set_step_status(step: int, status: str, error: str | None = None) -> dict | None:
    t = _load_raw()
    if not t or step >= len(t["steps"]):
        return None
    record = t["steps"][step]
    record["status"] = status
    if error:
        record["error"] = error
    _write(t)
    return t


def _replace_steps(t: dict, from_step: int, new_commands: list) -> list[dict]:
    kept = t["steps"][:from_step]
    previous_command = kept[-1]["cmd"] if kept else None
    return kept + build_step_records(normalize_commands(new_commands, previous_command=previous_command))


def _normalize_root(data: dict) -> dict:
    root = dict(data or {})
    for key in _DROPPED_KEYS:
        root.pop(key, None)
    # Older files kept a state snapshot per step; only cmd/status/error are used.
    root["steps"] = [
        {k: step[k] for k in ("cmd", "status", "error") if step.get(k) is not None}
        for step in root.get("steps") or [] if isinstance(step, dict) and step.get("cmd")
    ]
    root.setdefault("context", {})
    root["runtime"] = {k: v for k, v in (root.get("runtime") or {}).items() if k not in _DROPPED_RUNTIME_KEYS}
    root.setdefault("currentStep", 0)
    return root


def _for_archive(t: dict) -> dict:
    """The database row still stores the command list."""
    return {**t, "commands": commands(t)}


def _event(event_type: str, t: dict, reason: str | None, step: int) -> dict:
    steps = t["steps"]
    return {
        "type": event_type,
        "goal": t.get("goal"),
        "toGoal": t.get("goal"),
        "reason": reason,
        "command": steps[step]["cmd"] if 0 <= step < len(steps) else None,
        "step": step,
        "details": {},
        "at": _now_iso(),
    }


def _failure(goal: str | None, cmd: str | None, step: int | None, reason: str, activity: str | None) -> dict:
    return {"goal": goal, "command": cmd, "step": step, "reason": reason, "activity": activity, "at": _now_iso()}


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
