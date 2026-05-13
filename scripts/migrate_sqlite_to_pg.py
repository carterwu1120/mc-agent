#!/usr/bin/env python3
"""
One-time migration: copy all SQLite task history data into PostgreSQL.

Usage:
    DATABASE_URL=postgresql://mcagent:mcagent@localhost:5432/mcagent \
    AGENT_DATA_ROOT=./agent/data \
    python scripts/migrate_sqlite_to_pg.py

Idempotency: tasks use ON CONFLICT DO NOTHING. Events/failures/logs are skipped
entirely for a given bot if any rows already exist for that bot_id in PostgreSQL.
SQLite files are NOT deleted — they remain as backups.
"""
from __future__ import annotations
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
import os
import pathlib
import sqlite3
import sys
from datetime import datetime, timezone


def _parse_ts(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return datetime.now(timezone.utc)


def _decode_json(value, default):
    if value in (None, ""):
        return default
    try:
        return json.loads(value)
    except Exception:
        return default


async def migrate_bot(bot_id: str, db_path: pathlib.Path, pg_pool) -> dict[str, int]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    counts = {"tasks": 0, "events": 0, "failures": 0, "logs": 0}

    try:
        tasks = conn.execute("SELECT * FROM tasks").fetchall()
        for row in tasks:
            gv = row["goal_verified"] if "goal_verified" in row.keys() else None
            async with pg_pool.acquire() as pg:
                await pg.execute(
                    """
                    INSERT INTO tasks
                        (id, bot_id, goal, final_goal, commands, steps, status,
                         goal_verified, interrupted_by, created_at, finished_at)
                    VALUES ($1,$2,$3,$4,$5::jsonb,$6::jsonb,$7,$8,$9,$10,$11)
                    ON CONFLICT (id, bot_id) DO NOTHING
                    """,
                    row["id"], bot_id,
                    row["goal"] or "",
                    row["final_goal"],
                    json.dumps(_decode_json(row["commands"], [])),
                    json.dumps(_decode_json(row["steps"], [])),
                    row["status"] or "unknown",
                    None if gv is None else bool(gv),
                    row["interrupted_by"],
                    _parse_ts(row["created_at"]),
                    _parse_ts(row["finished_at"]) if row["finished_at"] else None,
                )
            counts["tasks"] += 1

        async with pg_pool.acquire() as pg:
            existing_events = await pg.fetchval("SELECT COUNT(*) FROM events WHERE bot_id = $1", bot_id)
        if existing_events > 0:
            print(f"    events already migrated for {bot_id} ({existing_events} rows), skipping")
        else:
            events = conn.execute("SELECT * FROM events").fetchall()
            for row in events:
                async with pg_pool.acquire() as pg:
                    await pg.execute(
                        """
                        INSERT INTO events
                            (task_id, bot_id, event_type, goal, to_goal, reason, command, step, details, at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10)
                        """,
                        row["task_id"], bot_id,
                        row["event_type"] or "",
                        row["goal"], row["to_goal"], row["reason"], row["command"], row["step"],
                        json.dumps(_decode_json(row["details"], {})),
                        _parse_ts(row["at"]),
                    )
                counts["events"] += 1

        async with pg_pool.acquire() as pg:
            existing_failures = await pg.fetchval("SELECT COUNT(*) FROM failures WHERE bot_id = $1", bot_id)
        if existing_failures > 0:
            print(f"    failures already migrated for {bot_id} ({existing_failures} rows), skipping")
        else:
            failures = conn.execute("SELECT * FROM failures").fetchall()
            for row in failures:
                async with pg_pool.acquire() as pg:
                    await pg.execute(
                        """
                        INSERT INTO failures
                            (task_id, bot_id, goal, command, step, reason, activity, at)
                        VALUES ($1,$2,$3,$4,$5,$6,$7,$8)
                        """,
                        row["task_id"], bot_id,
                        row["goal"], row["command"], row["step"],
                        row["reason"] or "",
                        row["activity"],
                        _parse_ts(row["at"]),
                    )
                counts["failures"] += 1

        async with pg_pool.acquire() as pg:
            existing_logs = await pg.fetchval("SELECT COUNT(*) FROM logs WHERE bot_id = $1", bot_id)
        if existing_logs > 0:
            print(f"    logs already migrated for {bot_id} ({existing_logs} rows), skipping")
        else:
            logs = conn.execute("SELECT * FROM logs").fetchall()
            for row in logs:
                async with pg_pool.acquire() as pg:
                    await pg.execute(
                        "INSERT INTO logs (time, level, service, bot_id, task_id, msg) VALUES ($1,$2,$3,$4,$5,$6)",
                        _parse_ts(row["time"]),
                        row["level"] or "INFO",
                        row["service"] or "",
                        row["bot_id"] or bot_id,
                        row["task_id"],
                        row["msg"] or "",
                    )
                counts["logs"] += 1

    finally:
        conn.close()

    return counts


async def main():
    import asyncpg
    from agent.db.schema import init_schema

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL is not set", file=sys.stderr)
        sys.exit(1)

    data_root = pathlib.Path(os.environ.get("AGENT_DATA_ROOT", "./agent/data"))
    if not data_root.exists():
        print(f"ERROR: data root {data_root} does not exist", file=sys.stderr)
        sys.exit(1)

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=5)
    await init_schema(pool)
    print("Schema ready.")

    bot_dirs = [d for d in data_root.iterdir() if d.is_dir()]
    if not bot_dirs:
        print("No bot directories found.")
        return

    total = {"tasks": 0, "events": 0, "failures": 0, "logs": 0}
    for bot_dir in sorted(bot_dirs):
        db_path = bot_dir / "task_history.db"
        if not db_path.exists():
            print(f"  {bot_dir.name}: no SQLite DB, skipping")
            continue
        bot_id = bot_dir.name
        print(f"  Migrating {bot_id} from {db_path} ...")
        counts = await migrate_bot(bot_id, db_path, pool)
        for k, v in counts.items():
            total[k] += v
        print(f"    → tasks={counts['tasks']} events={counts['events']} failures={counts['failures']} logs={counts['logs']}")

    await pool.close()
    print(f"\nDone. Total: tasks={total['tasks']} events={total['events']} failures={total['failures']} logs={total['logs']}")
    print("SQLite files are preserved as backups.")


if __name__ == "__main__":
    asyncio.run(main())
