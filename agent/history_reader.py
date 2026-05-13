"""
Multi-bot history reader — delegates to agent/db/reader.py (PostgreSQL).

Keeps the same public API so backend/services/history_repo.py and other
callers can switch to the async reader directly without breaking.
"""
from __future__ import annotations

import asyncio

from agent.db import reader as db_reader


def _run(coro):
    try:
        loop = asyncio.get_running_loop()
        # Already in an async context — return the coroutine for awaiting.
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)


# ── Sync wrappers (used by agent-side callers) ────────────────────────────────

def query_history_for_bot(bot_id: str, *, data_root=None, limit: int = 20, status: str | None = None) -> list[dict]:
    return _run(db_reader.query_history(bot_id=bot_id, limit=limit, status=status))


def query_failures_for_bot(bot_id: str, *, data_root=None, limit: int = 10, activity: str | None = None) -> list[dict]:
    return _run(db_reader.query_failures(bot_id=bot_id, limit=limit, activity=activity))


def query_events_for_bot(bot_id: str, *, data_root=None, limit: int = 20, event_type: str | None = None, task_id: str | None = None) -> list[dict]:
    return _run(db_reader.query_events(bot_id=bot_id, limit=limit, event_type=event_type, task_id=task_id))


def query_logs_for_bot(bot_id: str, *, data_root=None, task_id: str | None = None, limit: int = 100) -> list[dict]:
    return _run(db_reader.query_logs(bot_id=bot_id, task_id=task_id, limit=limit))


def find_task(task_id: str, *, data_root=None) -> dict | None:
    return _run(db_reader.find_task(task_id))


def query_metrics(*, data_root=None, since_hours: int = 24) -> dict:
    return _run(db_reader.query_metrics(since_hours=since_hours))


# ── Async versions (used directly by backend) ─────────────────────────────────

async def find_task_async(task_id: str, *, bot_id: str | None = None) -> dict | None:
    return await db_reader.find_task(task_id, bot_id=bot_id)


async def query_metrics_async(*, bot_id: str | None = None, since_hours: int = 24) -> dict:
    return await db_reader.query_metrics(bot_id=bot_id, since_hours=since_hours)
