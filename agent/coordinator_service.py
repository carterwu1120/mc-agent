from __future__ import annotations
import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Literal

from aiohttp import web

TaskStatus = Literal["queued", "running", "done", "failed"]


@dataclass
class Task:
    task_id: str
    bot_id: str
    commands: list[str]
    goal: str
    status: TaskStatus = "queued"
    interrupt: bool = False


_queues:          dict[str, asyncio.Queue] = {}        # bot_id → Queue[Task]
_tasks:           dict[str, Task]          = {}        # task_id → Task (idempotency registry)
_registered:      set[str]                 = set()
_interrupt_slots: dict[str, Task | None]   = {}        # bot_id → single pending interrupt task
_abort_flags:     dict[str, bool]          = {}        # bot_id → force-abort pending
_bot_last_seen:   dict[str, float]         = {}        # bot_id → time.time() of last heartbeat

_OFFLINE_THRESHOLD = 30.0   # seconds without heartbeat → bot considered offline
_MONITOR_INTERVAL  = 10.0   # how often the health monitor wakes

_CORS = {"Access-Control-Allow-Origin": "*"}


def _json(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        status=status,
        content_type="application/json",
        headers=_CORS,
    )


def _task_to_dict(task: Task | None) -> dict | None:
    if task is None:
        return None
    return {
        "task_id": task.task_id,
        "bot_id": task.bot_id,
        "commands": list(task.commands),
        "goal": task.goal,
        "status": task.status,
        "interrupt": task.interrupt,
    }


def _is_online(bot_id: str, now: float | None = None) -> bool:
    last = _bot_last_seen.get(bot_id, 0.0)
    return ((now or time.time()) - last) <= _OFFLINE_THRESHOLD


def _last_seen_iso(bot_id: str) -> str | None:
    last = _bot_last_seen.get(bot_id)
    if not last:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last))


def _queued_tasks(bot_id: str) -> list[dict]:
    queue = _queues.get(bot_id)
    if queue is None:
        return []
    return [_task_to_dict(task) for task in list(getattr(queue, "_queue", []))]


def _running_tasks(bot_id: str) -> list[dict]:
    return [
        _task_to_dict(task)
        for task in _tasks.values()
        if task.bot_id == bot_id and task.status == "running"
    ]


async def handle_register(request: web.Request) -> web.Response:
    body = await request.json()
    bot_id = body.get("bot_id", "")
    if not bot_id:
        return _json({"error": "bot_id required"}, 400)
    _registered.add(bot_id)
    if bot_id not in _queues:
        _queues[bot_id] = asyncio.Queue()
    _interrupt_slots[bot_id] = None
    _abort_flags[bot_id] = False
    _bot_last_seen[bot_id] = time.time()
    print(f"[CoordinatorService] Registered: {bot_id}")
    return _json({"ok": True})


async def handle_enqueue(request: web.Request) -> web.Response:
    bot_id = request.match_info["id"]
    body = await request.json()
    task_id = body.get("task_id", "")
    commands = body.get("commands") or []
    goal = body.get("goal", "")

    if not task_id:
        return _json({"error": "task_id required"}, 400)

    if task_id in _tasks:
        return _json({"ok": True, "status": "already_queued"})

    if bot_id not in _registered:
        return _json({"error": "bot not registered"}, 404)

    interrupt = bool(body.get("interrupt", False))
    task = Task(task_id=task_id, bot_id=bot_id, commands=commands, goal=goal, interrupt=interrupt)
    _tasks[task_id] = task
    if interrupt:
        _interrupt_slots[bot_id] = task
        print(f"[CoordinatorService] Interrupt-slot {task_id} for {bot_id}: {goal}")
    else:
        await _queues[bot_id].put(task)
        print(f"[CoordinatorService] Enqueued {task_id} for {bot_id}: {goal}")
    return _json({"ok": True, "task_id": task_id}, 201)


async def handle_next(request: web.Request) -> web.Response:
    """Peek at the next queued task without consuming it. Agent must call /claim to take ownership."""
    bot_id = request.match_info["id"]
    queue = _queues.get(bot_id)
    if queue is None:
        return _json({"task": None})
    items = list(getattr(queue, "_queue", []))
    if not items:
        return _json({"task": None})
    task = items[0]
    return _json({"task": {"task_id": task.task_id, "commands": task.commands, "goal": task.goal}})


async def handle_claim(request: web.Request) -> web.Response:
    """Atomically pop the task from the queue and mark it running. Returns 409 if already claimed."""
    bot_id = request.match_info["id"]
    task_id = request.match_info["task_id"]
    task = _tasks.get(task_id)
    if task is None:
        return _json({"error": "task not found"}, 404)
    if task.bot_id != bot_id:
        return _json({"error": "task bot_id mismatch"}, 400)
    if task.status != "queued":
        return _json({"error": f"not claimable: status={task.status!r}"}, 409)
    queue = _queues.get(bot_id)
    if queue is None:
        return _json({"error": "bot not registered"}, 404)
    # Verify it's still at the front (no await between status check and pop — asyncio single-threaded)
    items = list(getattr(queue, "_queue", []))
    if not items or items[0].task_id != task_id:
        return _json({"error": "task no longer at queue front"}, 409)
    queue.get_nowait()
    task.status = "running"
    print(f"[CoordinatorService] Task {task_id} claimed by {bot_id}")
    return _json({"ok": True})


async def handle_abort(request: web.Request) -> web.Response:
    bot_id = request.match_info["id"]
    if bot_id not in _registered:
        return _json({"error": "bot not registered"}, 404)
    _abort_flags[bot_id] = True
    print(f"[CoordinatorService] Abort flag set for {bot_id}")
    return _json({"ok": True})


async def handle_check_abort(request: web.Request) -> web.Response:
    bot_id = request.match_info["id"]
    flagged = _abort_flags.get(bot_id, False)
    if flagged:
        _abort_flags[bot_id] = False  # consume
    return _json({"abort": flagged})


async def handle_peek_interrupt(request: web.Request) -> web.Response:
    """Peek at the pending interrupt task without consuming it. Agent must call /interrupt/claim."""
    bot_id = request.match_info["id"]
    task = _interrupt_slots.get(bot_id)
    if task is None:
        return _json({"task": None})
    return _json({"task": {"task_id": task.task_id, "commands": task.commands, "goal": task.goal}})


async def handle_claim_interrupt(request: web.Request) -> web.Response:
    """Atomically consume the interrupt slot and mark task running. Returns 409 if already claimed."""
    bot_id = request.match_info["id"]
    task_id = request.match_info["task_id"]
    task = _interrupt_slots.get(bot_id)
    if task is None or task.task_id != task_id:
        return _json({"error": "interrupt task not found or superseded"}, 409)
    if task.status != "queued":
        return _json({"error": f"not claimable: status={task.status!r}"}, 409)
    _interrupt_slots[bot_id] = None
    task.status = "running"
    print(f"[CoordinatorService] Interrupt task {task_id} claimed by {bot_id}")
    return _json({"ok": True})


async def handle_release(request: web.Request) -> web.Response:
    """Return a claimed task back to the queue so it can be retried.
    Called by the agent when a post-claim local failure prevents the task from running."""
    bot_id = request.match_info["id"]
    task_id = request.match_info["task_id"]
    task = _tasks.get(task_id)
    if task is None or task.bot_id != bot_id:
        return _json({"error": "task not found"}, 404)
    if task.status != "running":
        return _json({"error": f"not releasable: status={task.status!r}"}, 409)
    queue = _queues.get(bot_id)
    if queue is None:
        return _json({"error": "bot not registered"}, 404)
    task.status = "queued"
    await queue.put(task)
    print(f"[CoordinatorService] Task {task_id} released back to queue by {bot_id}")
    return _json({"ok": True})


async def handle_update(request: web.Request) -> web.Response:
    task_id = request.match_info["task_id"]
    task = _tasks.get(task_id)
    if task is None:
        return _json({"error": "task not found"}, 404)
    body = await request.json()
    status = body.get("status", "")
    if status not in ("done", "failed"):
        return _json({"error": "status must be done or failed"}, 400)
    task.status = status
    print(f"[CoordinatorService] Task {task_id} → {status}")
    return _json({"ok": True})




async def handle_heartbeat(request: web.Request) -> web.Response:
    bot_id = request.match_info["id"]
    if bot_id not in _registered:
        return _json({"error": "bot not registered"}, 404)
    _bot_last_seen[bot_id] = time.time()
    return _json({"ok": True})


async def handle_internal_bots(request: web.Request) -> web.Response:
    now = time.time()
    bots = []
    for bot_id in sorted(_registered):
        bots.append(
            {
                "bot_id": bot_id,
                "registered": True,
                "online": _is_online(bot_id, now),
                "last_seen": _last_seen_iso(bot_id),
                "queued_count": len(_queued_tasks(bot_id)),
                "running_task_ids": [task["task_id"] for task in _running_tasks(bot_id)],
                "interrupt_task_id": ((_task_to_dict(_interrupt_slots.get(bot_id)) or {}).get("task_id")),
                "abort_flag": _abort_flags.get(bot_id, False),
            }
        )
    return _json({"bots": bots})


async def handle_internal_bot_tasks(request: web.Request) -> web.Response:
    bot_id = request.match_info["id"]
    if bot_id not in _registered:
        return _json({"error": "bot not registered"}, 404)
    return _json(
        {
            "bot_id": bot_id,
            "online": _is_online(bot_id),
            "last_seen": _last_seen_iso(bot_id),
            "running": _running_tasks(bot_id),
            "queued": _queued_tasks(bot_id),
            "interrupt": _task_to_dict(_interrupt_slots.get(bot_id)),
            "abort_flag": _abort_flags.get(bot_id, False),
        }
    )


async def handle_internal_task(request: web.Request) -> web.Response:
    task_id = request.match_info["task_id"]
    task = _tasks.get(task_id)
    if task is None:
        return _json({"task": None}, 404)
    payload = _task_to_dict(task)
    payload["online"] = _is_online(task.bot_id)
    payload["last_seen"] = _last_seen_iso(task.bot_id)
    return _json({"task": payload})


async def _monitor_bot_health() -> None:
    while True:
        await asyncio.sleep(_MONITOR_INTERVAL)
        now = time.time()
        for bot_id in list(_registered):
            last = _bot_last_seen.get(bot_id, 0.0)
            if now - last <= _OFFLINE_THRESHOLD:
                continue
            q = _queues.get(bot_id)
            if q is None:
                continue
            recovered = []
            while True:
                try:
                    task = q.get_nowait()
                    task.status = "failed"
                    recovered.append(task.task_id)
                except asyncio.QueueEmpty:
                    break
            if recovered:
                print(f"[CoordinatorService] {bot_id} offline — marked {len(recovered)} queued tasks failed: {recovered}")
            _bot_last_seen[bot_id] = now  # reset to suppress log spam until re-register


async def handle_health(request: web.Request) -> web.Response:
    return _json({"ok": True, "service": "coordinator"}, 200)


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/health", handle_health)
    app.router.add_post("/bots/register", handle_register)
    app.router.add_post("/bots/{id}/heartbeat", handle_heartbeat)
    app.router.add_post("/bots/{id}/tasks", handle_enqueue)
    app.router.add_get("/bots/{id}/tasks/next", handle_next)
    app.router.add_post("/bots/{id}/tasks/{task_id}/claim", handle_claim)
    app.router.add_post("/bots/{id}/abort", handle_abort)
    app.router.add_get("/bots/{id}/abort", handle_check_abort)
    app.router.add_get("/bots/{id}/tasks/interrupt", handle_peek_interrupt)
    app.router.add_post("/bots/{id}/tasks/{task_id}/claim-interrupt", handle_claim_interrupt)
    app.router.add_patch("/bots/{id}/tasks/{task_id}", handle_update)
    app.router.add_post("/bots/{id}/tasks/{task_id}/release", handle_release)
    app.router.add_get("/internal/bots", handle_internal_bots)
    app.router.add_get("/internal/bots/{id}/tasks", handle_internal_bot_tasks)
    app.router.add_get("/internal/tasks/{task_id}", handle_internal_task)
    return app


async def start(port: int | None = None) -> None:
    port = port or int(os.environ.get("COORDINATOR_PORT", 3010))
    try:
        app = create_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"[CoordinatorService] http://0.0.0.0:{port}")
        asyncio.create_task(_monitor_bot_health())
    except Exception as e:
        print(f"[CoordinatorService] 啟動失敗: {type(e).__name__}: {e}")
        return
    while True:
        await asyncio.sleep(3600)
