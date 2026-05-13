from __future__ import annotations

import json
import math
import pathlib
import sqlite3
from datetime import datetime, timedelta, timezone

from agent.state_reader import get_bot_dir, get_data_root, iter_bot_ids


def _db_path(bot_id: str, data_root: pathlib.Path | None = None) -> pathlib.Path:
    return get_bot_dir(bot_id, data_root) / "task_history.db"


def _connect(path: pathlib.Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _decode_json(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


def _task_row_to_dict(row: sqlite3.Row, bot_id: str) -> dict:
    item = dict(row)
    item["bot_id"] = bot_id
    item["commands"] = _decode_json(item.get("commands"), [])
    item["steps"] = _decode_json(item.get("steps"), [])
    if "goal_verified" in item:
        gv = item.get("goal_verified")
        item["goal_verified"] = None if gv is None else bool(gv)
    return item


def _event_row_to_dict(row: sqlite3.Row, bot_id: str) -> dict:
    item = dict(row)
    item["bot_id"] = bot_id
    item["details"] = _decode_json(item.get("details"), {})
    return item


def query_history_for_bot(
    bot_id: str,
    *,
    data_root: pathlib.Path | None = None,
    limit: int = 20,
    status: str | None = None,
) -> list[dict]:
    conn = _connect(_db_path(bot_id, data_root))
    if conn is None:
        return []
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM tasks WHERE status = ? ORDER BY finished_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM tasks ORDER BY finished_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [_task_row_to_dict(row, bot_id) for row in rows]
    finally:
        conn.close()


def query_failures_for_bot(
    bot_id: str,
    *,
    data_root: pathlib.Path | None = None,
    limit: int = 10,
    activity: str | None = None,
) -> list[dict]:
    conn = _connect(_db_path(bot_id, data_root))
    if conn is None:
        return []
    try:
        if activity:
            rows = conn.execute(
                "SELECT * FROM failures WHERE activity = ? ORDER BY at DESC LIMIT ?",
                (activity, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM failures ORDER BY at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{**dict(row), "bot_id": bot_id} for row in rows]
    finally:
        conn.close()


def query_events_for_bot(
    bot_id: str,
    *,
    data_root: pathlib.Path | None = None,
    limit: int = 20,
    event_type: str | None = None,
    task_id: str | None = None,
) -> list[dict]:
    conn = _connect(_db_path(bot_id, data_root))
    if conn is None:
        return []
    try:
        clauses: list[str] = []
        params: list = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if task_id:
            clauses.append("task_id = ?")
            params.append(task_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = conn.execute(
            f"SELECT * FROM events {where} ORDER BY at DESC LIMIT ?",
            params,
        ).fetchall()
        return [_event_row_to_dict(row, bot_id) for row in rows]
    finally:
        conn.close()


def query_logs_for_bot(
    bot_id: str,
    *,
    data_root: pathlib.Path | None = None,
    task_id: str | None = None,
    limit: int = 100,
) -> list[dict]:
    conn = _connect(_db_path(bot_id, data_root))
    if conn is None:
        return []
    try:
        if task_id:
            rows = conn.execute(
                "SELECT * FROM logs WHERE task_id = ? ORDER BY time DESC LIMIT ?",
                (task_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM logs ORDER BY time DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [{**dict(row), "bot_id": bot_id} for row in rows]
    finally:
        conn.close()


def find_task(task_id: str, *, data_root: pathlib.Path | None = None) -> dict | None:
    root = data_root or get_data_root()
    for bot_id in iter_bot_ids(root):
        conn = _connect(_db_path(bot_id, root))
        if conn is None:
            continue
        try:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            if row:
                return _task_row_to_dict(row, bot_id)
        finally:
            conn.close()
    return None


def query_metrics(*, data_root: pathlib.Path | None = None, since_hours: int = 24) -> dict:
    root = data_root or get_data_root()
    cutoff = datetime.now(timezone.utc) - timedelta(hours=since_hours)

    task_counts: dict[str, int] = {}
    goal_verified_total = 0
    total_tasks = 0
    stuck_by_reason: dict[str, int] = {}
    stuck_by_activity: dict[str, int] = {}
    llm_latencies: list[int] = []
    by_skill: dict[str, list[int]] = {}

    for bot_id in iter_bot_ids(root):
        conn = _connect(_db_path(bot_id, root))
        if conn is None:
            continue
        try:
            task_columns = _table_columns(conn, "tasks")
            task_select = "SELECT status, created_at"
            if "goal_verified" in task_columns:
                task_select = "SELECT status, goal_verified, created_at"
            task_rows = conn.execute(
                task_select + " FROM tasks"
            ).fetchall()
            for row in task_rows:
                created_at = _parse_time(row["created_at"])
                if created_at is None or created_at < cutoff:
                    continue
                status = row["status"] or "unknown"
                task_counts[status] = task_counts.get(status, 0) + 1
                total_tasks += 1
                if "goal_verified" in row.keys() and row["goal_verified"] == 1:
                    goal_verified_total += 1

            failure_rows = conn.execute("SELECT reason, activity, at FROM failures").fetchall()
            for row in failure_rows:
                at = _parse_time(row["at"])
                if at is None or at < cutoff:
                    continue
                reason = row["reason"] or ""
                activity = row["activity"]
                if reason:
                    stuck_by_reason[reason] = stuck_by_reason.get(reason, 0) + 1
                if activity:
                    stuck_by_activity[activity] = stuck_by_activity.get(activity, 0) + 1

            llm_rows = conn.execute(
                "SELECT details, at FROM events WHERE event_type = 'llm_call'"
            ).fetchall()
            for row in llm_rows:
                at = _parse_time(row["at"])
                if at is None or at < cutoff:
                    continue
                details = _decode_json(row["details"], {})
                latency_ms = details.get("latency_ms")
                if isinstance(latency_ms, (int, float)):
                    latency = int(latency_ms)
                    llm_latencies.append(latency)
                    skill = details.get("skill") or "unknown"
                    by_skill.setdefault(skill, []).append(latency)
        finally:
            conn.close()

    done_tasks = task_counts.get("done", 0)
    return {
        "since_hours": since_hours,
        "tasks": task_counts,
        "task_success_rate": round(done_tasks / total_tasks * 100, 1) if total_tasks else None,
        "goal_success_rate": round(goal_verified_total / total_tasks * 100, 1) if total_tasks else None,
        "stuck_by_reason": stuck_by_reason,
        "stuck_by_activity": stuck_by_activity,
        "llm_latency": {
            "count": len(llm_latencies),
            "avg_ms": round(sum(llm_latencies) / len(llm_latencies), 1) if llm_latencies else None,
            "p95_ms": _p95(llm_latencies),
            "by_skill": {
                skill: {
                    "count": len(values),
                    "avg_ms": round(sum(values) / len(values), 1) if values else None,
                }
                for skill, values in sorted(by_skill.items())
            },
        },
    }


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _p95(values: list[int]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    idx = max(0, math.ceil(len(ordered) * 0.95) - 1)
    return float(ordered[idx])
