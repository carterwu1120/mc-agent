from __future__ import annotations

import json
import os
import pathlib
from collections import Counter
from datetime import datetime, timedelta, timezone


_STALE_SNAPSHOT_THRESHOLD = timedelta(seconds=30)


def get_data_root() -> pathlib.Path:
    explicit = os.environ.get("AGENT_DATA_ROOT")
    if explicit:
        return pathlib.Path(explicit)
    bot_data_dir = os.environ.get("BOT_DATA_DIR")
    if bot_data_dir:
        return pathlib.Path(bot_data_dir).parent
    return pathlib.Path(__file__).parent / "data"


def get_bot_dir(bot_id: str, data_root: pathlib.Path | None = None) -> pathlib.Path:
    return (data_root or get_data_root()) / bot_id


def load_json_file(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_bot_bundle(bot_id: str, data_root: pathlib.Path | None = None) -> dict:
    bot_dir = get_bot_dir(bot_id, data_root)
    return {
        "snapshot": load_json_file(bot_dir / "live_state.json", None),
        "task": load_json_file(bot_dir / "task.json", None),
        "chests": load_json_file(bot_dir / "chests.json", []),
    }


def snapshot_is_fresh(snapshot: dict | None) -> bool:
    if not snapshot:
        return False
    updated_at = snapshot.get("updated_at")
    if not updated_at:
        return bool(snapshot.get("ws_connected"))
    try:
        dt = datetime.fromisoformat(str(updated_at).replace("Z", "+00:00"))
    except Exception:
        return bool(snapshot.get("ws_connected"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return (datetime.now(timezone.utc) - dt) <= _STALE_SNAPSHOT_THRESHOLD


def iter_bot_ids(data_root: pathlib.Path | None = None) -> list[str]:
    root = data_root or get_data_root()
    bot_ids: set[str] = set()
    for pattern in ("*/live_state.json", "*/task.json", "*/task_history.db"):
        for path in root.glob(pattern):
            bot_ids.add(path.parent.name)
    return sorted(bot_ids)


def format_task(task: dict | None) -> dict | None:
    if not task:
        return None
    steps = task.get("steps") or []
    idx = int(task.get("currentStep") or 0)
    pending = [
        step["cmd"]
        for step in steps[idx + 1 :]
        if step.get("status") in ("pending", "failed")
    ]
    return {
        "id": task.get("id"),
        "goal": task.get("goal"),
        "final_goal": task.get("final_goal"),
        "status": task.get("status"),
        "interruptedBy": task.get("interruptedBy"),
        "createdAt": task.get("createdAt"),
        "currentStep": idx,
        "totalSteps": len(steps),
        "currentCmd": steps[idx]["cmd"] if 0 <= idx < len(steps) else None,
        "pendingSteps": pending,
        "progress_pct": round(idx / len(steps) * 100) if steps else 0,
        "source": task.get("source"),
        "goalVerified": task.get("goalVerified"),
    }


def top_items(inventory: list, n: int = 12) -> list[dict]:
    counts: Counter = Counter()
    for item in inventory:
        name = item.get("name")
        if name:
            counts[name] += int(item.get("count", 0))
    return [{"name": name, "count": count} for name, count in counts.most_common(n)]


def format_chests(chests: list) -> list[dict]:
    out = []
    for chest in chests:
        contents = (chest.get("contents") or [])[:8]
        out.append(
            {
                "id": chest.get("id"),
                "label": chest.get("label") or "misc",
                "pos": chest.get("pos"),
                "freeSlots": chest.get("freeSlots"),
                "totalSlots": chest.get("totalSlots"),
                "usedSlots": chest.get("usedSlots"),
                "contents": contents,
                "updatedAt": chest.get("updatedAt"),
            }
        )
    return out


def build_bot_view(
    bot_id: str,
    *,
    snapshot: dict | None,
    task: dict | None,
    chests: list | None,
    recent_events: list[dict] | None = None,
    recent_failures: list[dict] | None = None,
    internal_state: dict | None = None,
    internal_detail: dict | None = None,
    expose_internal: bool = False,
) -> dict:
    snapshot = snapshot or {}
    task = task or {}
    chests = chests or []
    snapshot_online = bool(snapshot.get("ws_connected")) and snapshot_is_fresh(snapshot)
    online = (
        internal_state.get("online")
        if internal_state and internal_state.get("online") is not None
        else snapshot_online
    )
    state_updated_at = (
        internal_state.get("last_seen")
        if internal_state and internal_state.get("last_seen")
        else snapshot.get("updated_at")
    )
    recent_events = recent_events if recent_events is not None else (task.get("recentEvents") or [])[:5]
    recent_failures = recent_failures if recent_failures is not None else (task.get("recentFailures") or [])[:5]

    return {
        "id": bot_id,
        "name": snapshot.get("name", bot_id),
        "ws_connected": bool(online),
        "state_updated_at": state_updated_at,
        "status": {
            "activity": snapshot.get("activity") if online else None,
            "position": snapshot.get("pos"),
            "health": snapshot.get("health"),
            "food": snapshot.get("food"),
            "mode": snapshot.get("mode"),
            "home": snapshot.get("home"),
        },
        "current_task": format_task(task or None),
        "interrupted_tasks": [format_task(item) for item in (task.get("interruptedTasks") or [])[:3]],
        "equipment": snapshot.get("equipment") or {},
        "inventory": top_items(snapshot.get("inventory") or [], n=12),
        "inventory_slots": snapshot.get("inventory_slots") or {},
        "chests": format_chests(chests),
        "recent_events": list(recent_events)[:5],
        "recent_failures": list(recent_failures)[:5],
        "internal": internal_detail if expose_internal else None,
        "source": task.get("source"),
        "queued_count": (internal_state or {}).get("queued_count", 0),
        "online": bool(online),
        "last_seen": state_updated_at,
    }


def summarize_bot_view(bot_view: dict) -> dict:
    current_task = bot_view.get("current_task") or {}
    status = bot_view.get("status") or {}
    return {
        "bot_id": bot_view.get("id"),
        "name": bot_view.get("name"),
        "online": bot_view.get("online", False),
        "activity": status.get("activity"),
        "position": status.get("position"),
        "health": status.get("health"),
        "food": status.get("food"),
        "current_task": current_task.get("goal"),
        "queued_count": bot_view.get("queued_count", 0),
        "last_seen": bot_view.get("last_seen"),
        "source": bot_view.get("source"),
    }
