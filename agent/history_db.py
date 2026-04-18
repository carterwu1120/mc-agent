"""
SQLite-backed task history store.

Owns all DB logic — connection, schema, writes, queries.
task_memory.py and dashboard.py import from here; they never touch SQLite directly.

DB file: {BOT_DATA_DIR}/task_history.db  (same directory as task.json)
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone

_DATA_DIR = os.environ.get("BOT_DATA_DIR", os.path.join(os.path.dirname(__file__), "data"))
_DB_FILE = os.path.join(_DATA_DIR, "task_history.db")
_db_conn: sqlite3.Connection | None = None


def init(data_dir: str) -> None:
    """Override the data directory. Call before any DB operations (e.g. in tests)."""
    global _DATA_DIR, _DB_FILE, _db_conn
    _DATA_DIR = data_dir
    _DB_FILE = os.path.join(data_dir, "task_history.db")
    _db_conn = None  # reset so _get_db() recreates with new path


def _get_db() -> sqlite3.Connection:
    global _db_conn
    if _db_conn is None:
        os.makedirs(_DATA_DIR, exist_ok=True)
        _db_conn = sqlite3.connect(_DB_FILE, check_same_thread=False)
        _db_conn.row_factory = sqlite3.Row
        _init_schema(_db_conn)
    return _db_conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id             TEXT PRIMARY KEY,
            goal           TEXT NOT NULL,
            final_goal     TEXT,
            commands       TEXT NOT NULL,
            steps          TEXT NOT NULL,
            status         TEXT NOT NULL,
            interrupted_by TEXT,
            created_at     TEXT NOT NULL,
            finished_at    TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_tasks_status     ON tasks(status);
        CREATE INDEX IF NOT EXISTS idx_tasks_created_at ON tasks(created_at DESC);

        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id    TEXT,
            event_type TEXT NOT NULL,
            goal       TEXT,
            to_goal    TEXT,
            reason     TEXT,
            command    TEXT,
            step       INTEGER,
            details    TEXT,
            at         TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_task_id ON events(task_id);
        CREATE INDEX IF NOT EXISTS idx_events_at      ON events(at DESC);

        CREATE TABLE IF NOT EXISTS failures (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id  TEXT,
            goal     TEXT,
            command  TEXT,
            step     INTEGER,
            reason   TEXT NOT NULL,
            activity TEXT,
            at       TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_failures_task_id  ON failures(task_id);
        CREATE INDEX IF NOT EXISTS idx_failures_at       ON failures(at DESC);
        CREATE INDEX IF NOT EXISTS idx_failures_activity ON failures(activity);
    """)
    conn.commit()


def _fire_and_forget(fn, *args) -> None:
    """Run a synchronous DB function in the thread pool without blocking the event loop."""
    try:
        loop = asyncio.get_running_loop()
        loop.run_in_executor(None, fn, *args)
    except RuntimeError:
        fn(*args)  # fallback: no running loop (tests, sync context)


# ── Write functions ───────────────────────────────────────────────────────────

def archive_task(task: dict) -> None:
    """Archive a completed/failed/interrupted task snapshot to the tasks table."""
    try:
        conn = _get_db()
        conn.execute(
            """INSERT OR REPLACE INTO tasks
               (id, goal, final_goal, commands, steps, status, interrupted_by, created_at, finished_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                task.get("id"),
                task.get("goal") or "",
                task.get("final_goal"),
                json.dumps(task.get("commands") or [], ensure_ascii=False),
                json.dumps(task.get("steps") or [], ensure_ascii=False),
                task.get("status") or "unknown",
                task.get("interruptedBy"),
                task.get("createdAt") or "",
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"[HistoryDB] archive_task failed: {e}")


def write_event(event: dict, task_id: str | None) -> None:
    """Write an event record to the events table."""
    try:
        conn = _get_db()
        conn.execute(
            """INSERT INTO events
               (task_id, event_type, goal, to_goal, reason, command, step, details, at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                task_id,
                event.get("type") or "",
                event.get("goal"),
                event.get("toGoal"),
                event.get("reason"),
                event.get("command"),
                event.get("step"),
                json.dumps(event.get("details") or {}, ensure_ascii=False),
                event.get("at") or datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"[HistoryDB] write_event failed: {e}")


def write_failure(failure: dict, task_id: str | None) -> None:
    """Write a failure record to the failures table."""
    try:
        conn = _get_db()
        conn.execute(
            """INSERT INTO failures (task_id, goal, command, step, reason, activity, at)
               VALUES (?,?,?,?,?,?,?)""",
            (
                task_id,
                failure.get("goal"),
                failure.get("command"),
                failure.get("step"),
                failure.get("reason") or "",
                failure.get("activity"),
                failure.get("at") or datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"[HistoryDB] write_failure failed: {e}")


# ── Query functions ───────────────────────────────────────────────────────────

def query_history(limit: int = 20, status: str | None = None) -> list[dict]:
    conn = _get_db()
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
    return [dict(r) for r in rows]


def query_failures(limit: int = 10, activity: str | None = None) -> list[dict]:
    conn = _get_db()
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
    return [dict(r) for r in rows]


def query_events(
    limit: int = 20,
    event_type: str | None = None,
    task_id: str | None = None,
) -> list[dict]:
    conn = _get_db()
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
    return [dict(r) for r in rows]
