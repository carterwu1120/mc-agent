import asyncio
import socket
import time

import aiohttp

from agent import coordinator_service


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _reset_coordinator_state():
    coordinator_service._queues.clear()
    coordinator_service._tasks.clear()
    coordinator_service._registered.clear()
    coordinator_service._interrupt_slots.clear()
    coordinator_service._abort_flags.clear()
    coordinator_service._bot_last_seen.clear()


async def _with_server(test_coro):
    app = coordinator_service.create_app()
    runner = coordinator_service.web.AppRunner(app)
    await runner.setup()
    port = _free_port()
    site = coordinator_service.web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    base_url = f"http://127.0.0.1:{port}"
    try:
        async with aiohttp.ClientSession() as session:
            await test_coro(session, base_url)
    finally:
        await runner.cleanup()


def test_internal_task_transitions_from_queued_to_running():
    _reset_coordinator_state()

    async def scenario(session, base_url):
        resp = await session.post(f"{base_url}/bots/register", json={"bot_id": "bot0"})
        assert resp.status == 200

        resp = await session.post(
            f"{base_url}/bots/bot0/tasks",
            json={"task_id": "t1", "goal": "mine iron 8", "commands": ["mine iron 8"]},
        )
        assert resp.status == 201

        resp = await session.get(f"{base_url}/internal/tasks/t1")
        payload = await resp.json()
        assert resp.status == 200
        assert payload["task"]["status"] == "queued"

        resp = await session.get(f"{base_url}/bots/bot0/tasks/next")
        payload = await resp.json()
        assert payload["task"]["task_id"] == "t1"

        resp = await session.get(f"{base_url}/internal/bots/bot0/tasks")
        payload = await resp.json()
        assert resp.status == 200
        assert payload["queued"] == []
        assert [item["task_id"] for item in payload["running"]] == ["t1"]

    asyncio.run(_with_server(scenario))


def test_internal_bots_marks_stale_heartbeat_offline():
    _reset_coordinator_state()

    async def scenario(session, base_url):
        resp = await session.post(f"{base_url}/bots/register", json={"bot_id": "bot0"})
        assert resp.status == 200
        coordinator_service._bot_last_seen["bot0"] = time.time() - coordinator_service._OFFLINE_THRESHOLD - 1

        resp = await session.get(f"{base_url}/internal/bots")
        payload = await resp.json()
        assert resp.status == 200
        assert payload["bots"][0]["bot_id"] == "bot0"
        assert payload["bots"][0]["online"] is False

    asyncio.run(_with_server(scenario))
