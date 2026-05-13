from __future__ import annotations

import os

import asyncpg

_pool: asyncpg.Pool | None = None
_initialized: bool = False


async def init_pool() -> asyncpg.Pool:
    """Create the pool. Must be called once from the long-lived event loop (agent startup / FastAPI lifespan)."""
    global _pool, _initialized
    if _pool is None:
        dsn = os.environ.get("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL environment variable is not set")
        _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, command_timeout=10)
    _initialized = True
    return _pool


async def get_pool() -> asyncpg.Pool:
    """Return the pool. Raises if init_pool() has not been called yet."""
    if _pool is None:
        raise RuntimeError("DB pool not initialized — call init_pool() at startup before any writes")
    return _pool


async def close_pool() -> None:
    global _pool, _initialized
    if _pool is not None:
        await _pool.close()
        _pool = None
    _initialized = False


def is_ready() -> bool:
    """True once init_pool() has completed successfully."""
    return _initialized


def is_available() -> bool:
    return os.environ.get("DATABASE_URL") is not None
