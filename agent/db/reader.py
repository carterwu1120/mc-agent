from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from agent.db.pool import get_pool


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _row_to_dict(record) -> dict:
    d = dict(record)
    for k, v in d.items():
        if isinstance(v, datetime):
            d[k] = v.isoformat()
    return d


async def query_history(
    *,
    bot_id: str | None = None,
    limit: int = 20,
    status: str | None = None,
) -> list[dict]:
    pool = await get_pool()
    clauses = []
    params: list = []
    if bot_id:
        params.append(bot_id)
        clauses.append(f"bot_id = ${len(params)}")
    if status:
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM tasks {where} ORDER BY finished_at DESC NULLS LAST LIMIT ${len(params)}",
            *params,
        )
    return [_row_to_dict(r) for r in rows]


async def query_failures(
    *,
    bot_id: str | None = None,
    limit: int = 10,
    activity: str | None = None,
) -> list[dict]:
    pool = await get_pool()
    clauses = []
    params: list = []
    if bot_id:
        params.append(bot_id)
        clauses.append(f"bot_id = ${len(params)}")
    if activity:
        params.append(activity)
        clauses.append(f"activity = ${len(params)}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM failures {where} ORDER BY at DESC LIMIT ${len(params)}",
            *params,
        )
    return [_row_to_dict(r) for r in rows]


async def query_events(
    *,
    bot_id: str | None = None,
    limit: int = 20,
    event_type: str | None = None,
    task_id: str | None = None,
) -> list[dict]:
    pool = await get_pool()
    clauses = []
    params: list = []
    if bot_id:
        params.append(bot_id)
        clauses.append(f"bot_id = ${len(params)}")
    if event_type:
        params.append(event_type)
        clauses.append(f"event_type = ${len(params)}")
    if task_id:
        params.append(task_id)
        clauses.append(f"task_id = ${len(params)}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM events {where} ORDER BY at DESC LIMIT ${len(params)}",
            *params,
        )
    return [_row_to_dict(r) for r in rows]


async def query_logs(
    *,
    bot_id: str | None = None,
    task_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    pool = await get_pool()
    clauses = []
    params: list = []
    if bot_id:
        params.append(bot_id)
        clauses.append(f"bot_id = ${len(params)}")
    if task_id:
        params.append(task_id)
        clauses.append(f"task_id = ${len(params)}")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"SELECT * FROM logs {where} ORDER BY time DESC LIMIT ${len(params)}",
            *params,
        )
    return [_row_to_dict(r) for r in rows]


async def find_task(task_id: str, *, bot_id: str | None = None) -> dict | None:
    pool = await get_pool()
    params: list = [task_id]
    extra = ""
    if bot_id:
        params.append(bot_id)
        extra = f" AND bot_id = ${len(params)}"
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT * FROM tasks WHERE id = $1{extra} LIMIT 1", *params
        )
    return _row_to_dict(row) if row else None


async def query_metrics(
    *,
    bot_id: str | None = None,
    since_hours: int = 24,
) -> dict:
    pool = await get_pool()
    cutoff = _now_utc() - timedelta(hours=since_hours)
    bot_filter = ""
    bot_param: list = []
    if bot_id:
        bot_filter = " AND bot_id = $2"
        bot_param = [bot_id]

    async with pool.acquire() as conn:
        task_rows = await conn.fetch(
            f"SELECT status, COUNT(*) AS n, SUM(CASE WHEN goal_verified THEN 1 ELSE 0 END) AS gv"
            f" FROM tasks WHERE created_at >= $1{bot_filter} GROUP BY status",
            cutoff, *bot_param,
        )
        failure_reason_rows = await conn.fetch(
            f"SELECT reason, COUNT(*) AS n FROM failures"
            f" WHERE at >= $1 AND reason != ''{bot_filter} GROUP BY reason ORDER BY n DESC",
            cutoff, *bot_param,
        )
        failure_activity_rows = await conn.fetch(
            f"SELECT activity, COUNT(*) AS n FROM failures"
            f" WHERE at >= $1 AND activity IS NOT NULL{bot_filter} GROUP BY activity ORDER BY n DESC",
            cutoff, *bot_param,
        )
        llm_rows = await conn.fetch(
            f"SELECT details->>'skill' AS skill,"
            f"  ROUND(AVG((details->>'latency_ms')::float)) AS avg_ms,"
            f"  COUNT(*) AS n,"
            f"  PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY (details->>'latency_ms')::float) AS p95_ms"
            f" FROM events"
            f" WHERE event_type = 'llm_call' AND at >= $1 AND details->>'latency_ms' IS NOT NULL{bot_filter}"
            f" GROUP BY skill",
            cutoff, *bot_param,
        )

    task_counts: dict[str, int] = {}
    total_tasks = 0
    goal_verified_total = 0
    for row in task_rows:
        status = row["status"] or "unknown"
        n = row["n"]
        task_counts[status] = n
        total_tasks += n
        goal_verified_total += row["gv"] or 0

    done_tasks = task_counts.get("done", 0)

    all_latencies: list[float] = []
    by_skill: dict[str, dict] = {}
    for row in llm_rows:
        skill = row["skill"] or "unknown"
        n = row["n"]
        avg = float(row["avg_ms"]) if row["avg_ms"] is not None else None
        p95 = float(row["p95_ms"]) if row["p95_ms"] is not None else None
        by_skill[skill] = {"count": n, "avg_ms": avg, "p95_ms": p95}
        if avg is not None:
            all_latencies.extend([avg] * n)

    overall_avg = round(sum(all_latencies) / len(all_latencies), 1) if all_latencies else None
    ordered = sorted(all_latencies)
    p95_overall: float | None = None
    if ordered:
        idx = max(0, math.ceil(len(ordered) * 0.95) - 1)
        p95_overall = float(ordered[idx])

    return {
        "since_hours": since_hours,
        "tasks": task_counts,
        "task_success_rate": round(done_tasks / total_tasks * 100, 1) if total_tasks else None,
        "goal_success_rate": round(goal_verified_total / total_tasks * 100, 1) if total_tasks else None,
        "stuck_by_reason": {r["reason"]: r["n"] for r in failure_reason_rows},
        "stuck_by_activity": {r["activity"]: r["n"] for r in failure_activity_rows},
        "llm_latency": {
            "count": sum(v["count"] for v in by_skill.values()),
            "avg_ms": overall_avg,
            "p95_ms": p95_overall,
            "by_skill": by_skill,
        },
    }
