"""
Task history store — delegates to agent/db/ (PostgreSQL via asyncpg).

Keeps the same synchronous public API so task_memory.py and other callers
don't need to change. Writes are fire-and-forget async tasks.

DB connection requires DATABASE_URL env var pointing at PostgreSQL.
"""
from __future__ import annotations

import asyncio
import os

from agent.db import pool as db_pool
from agent.db import writer as db_writer

# BOT_ID is used to tag every write so rows can be filtered per-bot in the shared DB.
_BOT_ID = os.environ.get("BOT_ID", "bot0")

# Writes that arrive before init_db() completes are buffered here and flushed afterward.
_pending: list[tuple] = []


def _fire(coro) -> None:
    """Schedule a coroutine on the running event loop. Drops if pool not ready."""
    if not db_pool.is_ready():
        coro.close()
        return
    try:
        asyncio.ensure_future(coro)
    except RuntimeError:
        coro.close()


def _fire_or_buffer(make_coro, *args) -> None:
    """Schedule immediately if pool is ready, otherwise buffer for flush after init."""
    if db_pool.is_ready():
        try:
            asyncio.ensure_future(make_coro(*args))
        except RuntimeError:
            pass
    else:
        _pending.append((make_coro, args))


# ── Write functions (sync wrappers) ──────────────────────────────────────────

def archive_task(task: dict) -> None:
    _fire(db_writer.archive_task(task, _BOT_ID))


def write_event(event: dict, task_id: str | None) -> None:
    _fire(db_writer.write_event(event, task_id, _BOT_ID))


def write_failure(failure: dict, task_id: str | None) -> None:
    _fire(db_writer.write_failure(failure, task_id, _BOT_ID))


def write_log(entry: dict) -> None:
    # write_log fires before init_db() (logger is installed at module import time),
    # so it buffers instead of dropping.
    _fire_or_buffer(db_writer.write_log, entry)


# ── Init ─────────────────────────────────────────────────────────────────────

async def init_db() -> None:
    """Initialize the PostgreSQL schema. Call once at agent startup."""
    from agent.db.schema import init_schema
    pool = await db_pool.init_pool()
    await init_schema(pool)
    # Flush log entries that were written before the pool was ready.
    for make_coro, args in _pending:
        asyncio.ensure_future(make_coro(*args))
    _pending.clear()


def is_available() -> bool:
    return db_pool.is_available()
