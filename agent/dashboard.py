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

from aiohttp import web

from agent import task_memory
from agent import history_reader
from agent.state_reader import (
    build_bot_view,
    format_task,
    get_data_root,
    load_bot_bundle,
)

DATA_ROOT = get_data_root()
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


# ── Bot data builders ─────────────────────────────────────────────────────────

def _build_own_bot_data() -> dict:
    """Build bot data for the bot this process manages (live in-memory state)."""
    import datetime
    latest_state = _latest_state
    ws_connected = latest_state.get("health") is not None

    data_dir = DATA_ROOT / _own_bot_id
    chests = load_bot_bundle(_own_bot_id, DATA_ROOT)["chests"]
    snapshot = {
        **latest_state,
        "bot_id": _own_bot_id,
        "ws_connected": ws_connected,
        "updated_at": datetime.datetime.utcnow().isoformat() + "Z",
    }
    return build_bot_view(
        _own_bot_id,
        snapshot=snapshot,
        task=task_memory.load_any(),
        chests=chests,
        recent_events=task_memory.recent_events()[:5],
        recent_failures=task_memory.recent_failures()[:5],
        internal_detail={
            "thinking": sorted(_thinking),
            "queued_player_tasks": list(_queued_player_tasks),
            "recent_stuck_events": list(_recent_stuck_events),
        },
        expose_internal=True,
    )


def _build_remote_bot_data(bot_id: str, snapshot: dict) -> dict:
    """Build bot data for a remote bot, read from its live_state.json snapshot."""
    bundle = load_bot_bundle(bot_id, DATA_ROOT)
    return build_bot_view(
        bot_id,
        snapshot=snapshot,
        task=bundle["task"],
        chests=bundle["chests"],
    )


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
    from agent.skills.coordinator import _collect_all_bots_state
    coordinator_bots = _collect_all_bots_state()
    return {
        "coordinator": {
            "bots": [{"bot_id": b["bot_id"], "activity": b["activity"], "current_task": b["current_task"]} for b in coordinator_bots],
        },
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
        rows   = await _run_sync(
            history_reader.query_history_for_bot,
            _own_bot_id,
            data_root=DATA_ROOT,
            limit=limit,
            status=status,
        )
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500,
                            content_type="application/json", headers=_CORS)
    return web.Response(text=json.dumps(rows, ensure_ascii=False, default=str),
                        content_type="application/json", headers=_CORS)


async def handle_failures(request: web.Request) -> web.Response:
    try:
        limit    = min(int(request.rel_url.query.get("limit", 10)), 100)
        activity = request.rel_url.query.get("activity") or None
        rows     = await _run_sync(
            history_reader.query_failures_for_bot,
            _own_bot_id,
            data_root=DATA_ROOT,
            limit=limit,
            activity=activity,
        )
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
        rows       = await _run_sync(
            history_reader.query_events_for_bot,
            _own_bot_id,
            data_root=DATA_ROOT,
            limit=limit,
            event_type=event_type,
            task_id=task_id,
        )
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500,
                            content_type="application/json", headers=_CORS)
    return web.Response(text=json.dumps(rows, ensure_ascii=False, default=str),
                        content_type="application/json", headers=_CORS)


async def handle_metrics(request: web.Request) -> web.Response:
    try:
        hours = min(int(request.rel_url.query.get("hours", 24)), 168)
        data  = await _run_sync(history_reader.query_metrics, data_root=DATA_ROOT, since_hours=hours)
    except Exception as e:
        return web.Response(text=json.dumps({"error": str(e)}), status=500,
                            content_type="application/json", headers=_CORS)
    return web.Response(text=json.dumps(data, ensure_ascii=False),
                        content_type="application/json", headers=_CORS)


async def handle_logs(request: web.Request) -> web.Response:
    try:
        limit   = min(int(request.rel_url.query.get("limit", 100)), 500)
        task_id = request.rel_url.query.get("task_id") or None
        rows    = await _run_sync(
            history_reader.query_logs_for_bot,
            _own_bot_id,
            data_root=DATA_ROOT,
            task_id=task_id,
            limit=limit,
        )
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
        app.router.add_get("/logs", handle_logs)
        app.router.add_get("/metrics", handle_metrics)
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
