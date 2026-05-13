"""
Lightweight HTTP dashboard page server for the MC Agent.
Runs as a background asyncio task alongside the main WebSocket loop.

Endpoints:
  GET /       — serve dashboard.html

Port: DASHBOARD_PORT env var (default 3002)

The dashboard page fetches runtime data from the backend service rather than
serving its own public query API.
"""
from __future__ import annotations

import asyncio
import os
import pathlib

from aiohttp import web

HTML_FILE = pathlib.Path(__file__).parent / "dashboard.html"


def init(
    state: dict,
    thinking: set,
    queued_tasks,
    stuck_events,
    bot_id: str = "bot0",
) -> None:
    """Legacy no-op kept so agent startup does not need to change."""
    return None


async def handle_index(request: web.Request) -> web.Response:
    try:
        html = HTML_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        html = "<h1>dashboard.html not found</h1><p>Place dashboard.html in agent/</p>"
    return web.Response(text=html, content_type="text/html")


async def start(port: int | None = None) -> None:
    port = port or int(os.environ.get("DASHBOARD_PORT", 3002))
    try:
        app = web.Application()
        app.router.add_get("/", handle_index)
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
