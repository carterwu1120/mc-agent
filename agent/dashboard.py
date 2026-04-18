"""
Lightweight HTTP dashboard server for the MC Agent.
Runs as a background asyncio task alongside the main WebSocket loop.

Endpoints:
  GET /       — serve dashboard.html
  GET /state  — JSON snapshot of all agent state (multi-agent ready schema)

Port: DASHBOARD_PORT env var (default 3002)

Multi-agent: own bot's state comes from in-memory refs (live).
Other bots' state is read from {DATA_ROOT}/{bot_id}/live_state.json written
by each agent process on every WebSocket tick.
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import pathlib
from collections import Counter

from aiohttp import web

from agent import task_memory
from agent import history_db

DATA_ROOT = pathlib.Path(__file__).parent / "data"
HTML_FILE = pathlib.Path(__file__).parent / "dashboard.html"

_CORS = {"Access-Control-Allow-Origin": "*"}


# ── Shared state references (set by agent.py via init() before start()) ───────

_own_bot_id: str = "bot0"
_latest_state: dict = {}
_thinking: set = set()
_queued_player_tasks: "collections.deque" = collections.deque()
_recent_stuck_events: "collections.deque" = collections.deque()


def init(
    state: dict,
    thinking: set,
    queued_tasks,
    stuck_events,
    bot_id: str = "bot0",
) -> None:
    """Called by agent.py at startup to bind shared mutable containers."""
    global _own_bot_id, _latest_state, _thinking, _queued_player_tasks, _recent_stuck_events
    _own_bot_id          = bot_id
    _latest_state        = state
    _thinking            = thinking
    _queued_player_tasks = queued_tasks
    _recent_stuck_events = stuck_events


# ── Data helpers ──────────────────────────────────────────────────────────────

def _load_json_file(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _format_task(task: dict | None) -> dict | None:
    if not task:
        return None
    steps = task.get("steps") or []
    idx   = int(task.get("currentStep") or 0)
    pending = [
        s["cmd"] for s in steps[idx + 1:]
        if s.get("status") in ("pending", "failed")
    ]
    return {
        "id":           task.get("id"),
        "goal":         task.get("goal"),
        "final_goal":   task.get("final_goal"),
        "status":       task.get("status"),
        "interruptedBy":task.get("interruptedBy"),
        "createdAt":    task.get("createdAt"),
        "currentStep":  idx,
        "totalSteps":   len(steps),
        "currentCmd":   steps[idx]["cmd"] if 0 <= idx < len(steps) else None,
        "pendingSteps": pending,
        "progress_pct": round(idx / len(steps) * 100) if steps else 0,
    }


def _top_items(inventory: list, n: int = 12) -> list:
    counts: Counter = Counter()
    for item in inventory:
        name = item.get("name")
        if name:
            counts[name] += int(item.get("count", 0))
    return [{"name": k, "count": v} for k, v in counts.most_common(n)]


def _format_chests(chests: list) -> list:
    out = []
    for c in chests:
        contents = (c.get("contents") or [])[:8]
        out.append({
            "id":        c.get("id"),
            "label":     c.get("label") or "misc",
            "pos":       c.get("pos"),
            "freeSlots": c.get("freeSlots"),
            "totalSlots":c.get("totalSlots"),
            "usedSlots": c.get("usedSlots"),
            "contents":  contents,
            "updatedAt": c.get("updatedAt"),
        })
    return out


# ── Bot data builders ─────────────────────────────────────────────────────────

def _build_own_bot_data() -> dict:
    """Build bot data for the bot this process manages (live in-memory state)."""
    import datetime
    latest_state = _latest_state
    ws_connected = latest_state.get("health") is not None

    data_dir = DATA_ROOT / _own_bot_id
    chests = _load_json_file(data_dir / "chests.json", [])

    return {
        "id":   _own_bot_id,
        "name": latest_state.get("name", _own_bot_id),
        "ws_connected": ws_connected,
        "state_updated_at": datetime.datetime.utcnow().isoformat() + "Z",
        "status": {
            "activity": latest_state.get("activity") if ws_connected else None,
            "position": latest_state.get("pos"),
            "health":   latest_state.get("health"),
            "food":     latest_state.get("food"),
            "mode":     latest_state.get("mode"),
            "home":     latest_state.get("home"),
        },
        "current_task":      _format_task(task_memory.load_any()),
        "interrupted_tasks": [_format_task(t) for t in task_memory.interrupted_tasks()[:3]],
        "equipment":         latest_state.get("equipment") or {},
        "inventory":         _top_items(latest_state.get("inventory") or [], n=12),
        "inventory_slots":   latest_state.get("inventory_slots") or {},
        "chests":            _format_chests(chests),
        "recent_events":     task_memory.recent_events()[:5],
        "recent_failures":   task_memory.recent_failures()[:5],
        "internal": {
            "thinking":            sorted(_thinking),
            "queued_player_tasks": list(_queued_player_tasks),
            "recent_stuck_events": list(_recent_stuck_events),
        },
    }


def _build_remote_bot_data(bot_id: str, snapshot: dict) -> dict:
    """Build bot data for a remote bot, read from its live_state.json snapshot."""
    data_dir = DATA_ROOT / bot_id
    task   = _load_json_file(data_dir / "task.json", None)
    chests = _load_json_file(data_dir / "chests.json", [])

    # Reconstruct interrupted_tasks list from task.json's interruptedTasks field
    interrupted_raw = task.get("interruptedTasks", []) if task else []
    interrupted = [_format_task(t) for t in interrupted_raw[:3]]

    # Recent events / failures from task.json
    recent_events   = (task.get("recentEvents",   []) if task else [])[:5]
    recent_failures = (task.get("recentFailures", []) if task else [])[:5]

    ws_connected = snapshot.get("ws_connected", False)
    return {
        "id":   bot_id,
        "name": snapshot.get("name", bot_id),
        "ws_connected": ws_connected,
        "state_updated_at": snapshot.get("updated_at"),
        "status": {
            "activity": snapshot.get("activity") if ws_connected else None,
            "position": snapshot.get("pos"),
            "health":   snapshot.get("health"),
            "food":     snapshot.get("food"),
            "mode":     snapshot.get("mode"),
            "home":     snapshot.get("home"),
        },
        "current_task":      _format_task(task),
        "interrupted_tasks": interrupted,
        "equipment":         snapshot.get("equipment") or {},
        "inventory":         _top_items(snapshot.get("inventory") or [], n=12),
        "inventory_slots":   snapshot.get("inventory_slots") or {},
        "chests":            _format_chests(chests),
        "recent_events":     recent_events,
        "recent_failures":   recent_failures,
        "internal":          None,  # internal state not exposed for remote bots
    }


def _collect_all_bots() -> list[dict]:
    """Aggregate own bot (from memory) + all other bots (from live_state.json files)."""
    bots = [_build_own_bot_data()]

    for live_file in sorted(DATA_ROOT.glob("*/live_state.json")):
        bot_id = live_file.parent.name
        if bot_id == _own_bot_id:
            continue  # own bot already included from memory
        try:
            snapshot = json.loads(live_file.read_text(encoding="utf-8"))
            bots.append(_build_remote_bot_data(bot_id, snapshot))
        except Exception:
            pass

    return bots


def _build_state() -> dict:
    return {
        # Coordinator placeholder — null until multi-agent coordinator is implemented.
        # Future shape: { "assigned_tasks": [], "active_bots": [], "pending_decisions": [] }
        "coordinator": None,
        "agents": _collect_all_bots(),
    }


# ── Route handlers ────────────────────────────────────────────────────────────

async def _run_sync(fn, *args, **kwargs):
    import functools
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(fn, *args, **kwargs))


async def handle_history(request: web.Request) -> web.Response:
    try:
        limit  = min(int(request.rel_url.query.get("limit", 20)), 100)
        status = request.rel_url.query.get("status") or None
        rows   = await _run_sync(history_db.query_history, limit=limit, status=status)
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500,
                            content_type="application/json", headers=_CORS)
    return web.Response(text=json.dumps(rows, ensure_ascii=False, default=str),
                        content_type="application/json", headers=_CORS)


async def handle_failures(request: web.Request) -> web.Response:
    try:
        limit    = min(int(request.rel_url.query.get("limit", 10)), 100)
        activity = request.rel_url.query.get("activity") or None
        rows     = await _run_sync(history_db.query_failures, limit=limit, activity=activity)
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500,
                            content_type="application/json", headers=_CORS)
    return web.Response(text=json.dumps(rows, ensure_ascii=False, default=str),
                        content_type="application/json", headers=_CORS)


async def handle_events(request: web.Request) -> web.Response:
    try:
        limit      = min(int(request.rel_url.query.get("limit", 20)), 100)
        event_type = request.rel_url.query.get("type") or None
        task_id    = request.rel_url.query.get("task_id") or None
        rows       = await _run_sync(history_db.query_events, limit=limit,
                                     event_type=event_type, task_id=task_id)
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500,
                            content_type="application/json", headers=_CORS)
    return web.Response(text=json.dumps(rows, ensure_ascii=False, default=str),
                        content_type="application/json", headers=_CORS)


async def handle_state(request: web.Request) -> web.Response:
    try:
        data = _build_state()
    except Exception as e:
        return web.Response(
            text=json.dumps({"error": str(e)}),
            status=500,
            content_type="application/json",
            headers=_CORS,
        )
    return web.Response(
        text=json.dumps(data, ensure_ascii=False, default=str),
        content_type="application/json",
        headers=_CORS,
    )


async def handle_index(request: web.Request) -> web.Response:
    try:
        html = HTML_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        html = "<h1>dashboard.html not found</h1><p>Place dashboard.html in agent/</p>"
    return web.Response(text=html, content_type="text/html")


# ── Entry point ───────────────────────────────────────────────────────────────

async def start(port: int | None = None) -> None:
    port = port or int(os.environ.get("DASHBOARD_PORT", 3002))
    try:
        app = web.Application()
        app.router.add_get("/", handle_index)
        app.router.add_get("/state", handle_state)
        app.router.add_get("/history", handle_history)
        app.router.add_get("/failures", handle_failures)
        app.router.add_get("/events", handle_events)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"[Dashboard] http://localhost:{port}")
    except Exception as e:
        print(f"[Dashboard] 啟動失敗: {type(e).__name__}: {e}")
        return
    while True:
        await asyncio.sleep(3600)
