from __future__ import annotations
import asyncio
import json
import os
from dataclasses import dataclass, field
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

_CORS = {"Access-Control-Allow-Origin": "*"}


def _json(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        status=status,
        content_type="application/json",
        headers=_CORS,
    )


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
    print(f"[CoordinatorService] Registered: {bot_id}")
    return _json({"ok": True})


async def handle_enqueue(request: web.Request) -> web.Response:
    bot_id = request.match_info["id"]
    body = await request.json()
    task_id = body.get("task_id", "")
    commands = body.get("commands") or []
    goal = body.get("goal", "")

    if not task_id or not commands:
        return _json({"error": "task_id and commands required"}, 400)

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
    bot_id = request.match_info["id"]
    queue = _queues.get(bot_id)
    if queue is None:
        return _json({"task": None})
    try:
        task = queue.get_nowait()
    except asyncio.QueueEmpty:
        return _json({"task": None})
    task.status = "running"
    return _json({"task": {"task_id": task.task_id, "commands": task.commands, "goal": task.goal}})


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
    bot_id = request.match_info["id"]
    task = _interrupt_slots.get(bot_id)
    if task is None:
        return _json({"task": None})
    _interrupt_slots[bot_id] = None  # consume
    task.status = "running"
    return _json({"task": {"task_id": task.task_id, "commands": task.commands, "goal": task.goal}})


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


async def start(port: int | None = None) -> None:
    port = port or int(os.environ.get("COORDINATOR_PORT", 3010))
    try:
        app = web.Application()
        app.router.add_post("/bots/register", handle_register)
        app.router.add_post("/bots/{id}/tasks", handle_enqueue)
        app.router.add_get("/bots/{id}/tasks/next", handle_next)
        app.router.add_post("/bots/{id}/abort", handle_abort)
        app.router.add_get("/bots/{id}/abort", handle_check_abort)
        app.router.add_get("/bots/{id}/tasks/interrupt", handle_peek_interrupt)
        app.router.add_patch("/bots/{id}/tasks/{task_id}", handle_update)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        print(f"[CoordinatorService] http://0.0.0.0:{port}")
    except Exception as e:
        print(f"[CoordinatorService] 啟動失敗: {type(e).__name__}: {e}")
        return
    while True:
        await asyncio.sleep(3600)
