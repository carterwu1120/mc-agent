import json
import os
import uuid
from datetime import datetime
from agent.plan_utils import build_step_records, normalize_commands

FILE = os.path.join(os.path.dirname(__file__), 'data', 'task.json')


def save(goal: str, commands: list) -> dict:
    commands = normalize_commands(commands)
    task = {
        "id": uuid.uuid4().hex[:8],
        "goal": goal,
        "commands": commands,
        "steps": build_step_records(commands),
        "currentStep": 0,
        "context": {},
        "status": "running",
        "interruptedBy": None,
        "createdAt": datetime.utcnow().isoformat(),
    }
    _write(task)
    return task


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
        # 任何狀態下，只要還有 pending 步驟就視為可恢復
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
            return json.load(f)
    except Exception:
        return None


def _write(data: dict) -> None:
    with open(FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _patch(patch: dict) -> None:
    t = _load_raw()
    if t is None:
        return
    t.update(patch)
    _write(t)
