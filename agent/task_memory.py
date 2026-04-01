import json
import os
import uuid
from datetime import datetime

FILE = os.path.join(os.path.dirname(__file__), 'data', 'task.json')


def save(goal: str, commands: list) -> dict:
    task = {
        "id": uuid.uuid4().hex[:8],
        "goal": goal,
        "commands": commands,
        "steps": [{"cmd": cmd, "status": "pending", "error": None} for cmd in commands],
        "currentStep": 0,
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


def replace_remaining_steps(from_step: int, new_commands: list) -> None:
    t = _load_raw()
    if t is None:
        return
    kept = t.get("steps", [])[:from_step]
    new_steps = [{"cmd": cmd, "status": "pending", "error": None} for cmd in new_commands]
    t["steps"] = kept + new_steps
    t["commands"] = [s["cmd"] for s in t["steps"]]
    _write(t)


def interrupt(reason: str) -> None:
    _patch({"status": "interrupted", "interruptedBy": reason})


def done() -> None:
    _patch({"status": "done"})


def failed() -> None:
    _patch({"status": "failed"})


def load() -> dict | None:
    try:
        t = _load_raw()
        if t is None:
            return None
        return t if t.get("status") in ("running", "interrupted") else None
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
