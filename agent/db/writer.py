from __future__ import annotations

import json
from datetime import datetime, timezone

from agent.db.pool import get_pool


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return _now_utc()
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return _now_utc()


def _as_jsonb(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value or [], ensure_ascii=False)


async def archive_task(task: dict, bot_id: str) -> None:
    pool = await get_pool()
    goal_verified = task.get("goalVerified")
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tasks
                (id, bot_id, goal, final_goal, commands, steps, status,
                 goal_verified, interrupted_by, created_at, finished_at)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9,$10,$11)
            ON CONFLICT (id, bot_id) DO UPDATE SET
                goal           = EXCLUDED.goal,
                final_goal     = EXCLUDED.final_goal,
                commands       = EXCLUDED.commands,
                steps          = EXCLUDED.steps,
                status         = EXCLUDED.status,
                goal_verified  = EXCLUDED.goal_verified,
                interrupted_by = EXCLUDED.interrupted_by,
                finished_at    = EXCLUDED.finished_at
            """,
            task.get("id"),
            bot_id,
            task.get("goal") or "",
            task.get("final_goal"),
            _as_jsonb(task.get("commands") or []),
            _as_jsonb(task.get("steps") or []),
            task.get("status") or "unknown",
            None if goal_verified is None else bool(goal_verified),
            task.get("interruptedBy"),
            _parse_ts(task.get("createdAt")),
            _now_utc(),
        )


async def write_event(event: dict, task_id: str | None, bot_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO events
                (task_id, bot_id, event_type, goal, to_goal, reason, command, step, details, at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
            """,
            task_id,
            bot_id,
            event.get("type") or "",
            event.get("goal"),
            event.get("toGoal"),
            event.get("reason"),
            event.get("command"),
            event.get("step"),
            _as_jsonb(event.get("details") or {}),
            _parse_ts(event.get("at")),
        )


async def write_failure(failure: dict, task_id: str | None, bot_id: str) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO failures
                (task_id, bot_id, goal, command, step, reason, activity, at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
            """,
            task_id,
            bot_id,
            failure.get("goal"),
            failure.get("command"),
            failure.get("step"),
            failure.get("reason") or "",
            failure.get("activity"),
            _parse_ts(failure.get("at")),
        )


async def write_log(entry: dict) -> None:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO logs (time, level, service, bot_id, task_id, msg) VALUES ($1,$2,$3,$4,$5,$6)",
            _parse_ts(entry.get("time")),
            entry.get("level") or "INFO",
            entry.get("service") or "",
            entry.get("bot_id") or "",
            entry.get("task_id"),
            entry.get("msg") or "",
        )
