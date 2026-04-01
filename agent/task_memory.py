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
        "currentStep": 0,
        "status": "running",
        "interruptedBy": None,
        "createdAt": datetime.utcnow().isoformat(),
    }
    _write(task)
    return task


def update_step(step: int) -> None:
    _patch({"currentStep": step})


def interrupt(reason: str) -> None:
    _patch({"status": "interrupted", "interruptedBy": reason})


def done() -> None:
    _patch({"status": "done"})


def failed() -> None:
    _patch({"status": "failed"})


def load() -> dict | None:
    try:
        with open(FILE, 'r', encoding='utf-8') as f:
            t = json.load(f)
        return t if t.get("status") in ("running", "interrupted") else None
    except Exception:
        return None


def clear() -> None:
    try:
        os.remove(FILE)
    except Exception:
        pass


def _write(data: dict) -> None:
    with open(FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _patch(patch: dict) -> None:
    t = load()
    if t is None:
        return
    t.update(patch)
    _write(t)
