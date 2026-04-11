"""
Lightweight HTTP dashboard server for the MC Agent.
Runs as a background asyncio task alongside the main WebSocket loop.

Endpoints:
  GET /       — serve dashboard.html
  GET /state  — JSON snapshot of all agent state (multi-agent ready schema)

Port: DASHBOARD_PORT env var (default 3002)
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

CHESTS_FILE = pathlib.Path(__file__).parent / "data" / "chests.json"
HTML_FILE   = pathlib.Path(__file__).parent / "dashboard.html"

_CORS = {"Access-Control-Allow-Origin": "*"}


# ── Shared state references (set by agent.py via init() before start()) ───────

_latest_state: dict = {}
_thinking: set = set()
_queued_player_tasks: "collections.deque" = collections.deque()
_recent_stuck_events: "collections.deque" = collections.deque()


def init(
    state: dict,
    thinking: set,
    queued_tasks,
    stuck_events,
) -> None:
    """Called by agent.py at startup to bind shared mutable containers."""
    global _latest_state, _thinking, _queued_player_tasks, _recent_stuck_events
    _latest_state      = state
    _thinking          = thinking
    _queued_player_tasks = queued_tasks
    _recent_stuck_events = stuck_events


def _get_agent_globals() -> tuple:
    return _latest_state, _thinking, _queued_player_tasks, _recent_stuck_events


# ── Data helpers ──────────────────────────────────────────────────────────────

def _load_chests() -> list:
    try:
        return json.loads(CHESTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


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


def _build_state() -> dict:
    import datetime
    latest_state, thinking, queued_tasks, recent_stuck = _get_agent_globals()
    task        = task_memory.load_any()
    interrupted = task_memory.interrupted_tasks()[:3]
    chests      = _load_chests()

    # Detect if we have real live data (health is always present in a real tick)
    ws_connected = latest_state.get("health") is not None

    bot_data = {
        "id":   "bot0",
        "name": "Agent",
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
        "current_task":      _format_task(task),
        "interrupted_tasks": [_format_task(t) for t in interrupted],
        "equipment":         latest_state.get("equipment") or {},
        "inventory":         _top_items(latest_state.get("inventory") or [], n=12),
        "inventory_slots":   latest_state.get("inventory_slots") or {},
        "chests":            _format_chests(chests),
        "recent_events":     task_memory.recent_events()[:5],
        "recent_failures":   task_memory.recent_failures()[:5],
        "internal": {
            "thinking":            sorted(thinking),
            "queued_player_tasks": list(queued_tasks),
            "recent_stuck_events": list(recent_stuck),
        },
    }

    return {
        # Coordinator placeholder — null until multi-agent coordinator is implemented.
        # Future shape: { "assigned_tasks": [], "active_bots": [], "pending_decisions": [] }
        "coordinator": None,
        "agents": [bot_data],
    }


# ── Route handlers ────────────────────────────────────────────────────────────

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
