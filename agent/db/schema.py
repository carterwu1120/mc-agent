from __future__ import annotations

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS tasks (
    id             TEXT        NOT NULL,
    bot_id         TEXT        NOT NULL,
    goal           TEXT        NOT NULL,
    final_goal     TEXT,
    commands       JSONB       NOT NULL DEFAULT '[]',
    steps          JSONB       NOT NULL DEFAULT '[]',
    status         TEXT        NOT NULL,
    goal_verified  BOOLEAN,
    interrupted_by TEXT,
    created_at     TIMESTAMPTZ NOT NULL,
    finished_at    TIMESTAMPTZ,
    PRIMARY KEY (id, bot_id)
);
CREATE INDEX IF NOT EXISTS idx_tasks_bot_id  ON tasks(bot_id);
CREATE INDEX IF NOT EXISTS idx_tasks_status  ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_created ON tasks(created_at DESC);

CREATE TABLE IF NOT EXISTS events (
    id         BIGSERIAL   PRIMARY KEY,
    task_id    TEXT,
    bot_id     TEXT        NOT NULL,
    event_type TEXT        NOT NULL,
    goal       TEXT,
    to_goal    TEXT,
    reason     TEXT,
    command    TEXT,
    step       INTEGER,
    details    JSONB,
    at         TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_bot_task ON events(bot_id, task_id);
CREATE INDEX IF NOT EXISTS idx_events_at       ON events(at DESC);
CREATE INDEX IF NOT EXISTS idx_events_type     ON events(event_type);

CREATE TABLE IF NOT EXISTS failures (
    id       BIGSERIAL   PRIMARY KEY,
    task_id  TEXT,
    bot_id   TEXT        NOT NULL,
    goal     TEXT,
    command  TEXT,
    step     INTEGER,
    reason   TEXT        NOT NULL,
    activity TEXT,
    at       TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_failures_bot  ON failures(bot_id);
CREATE INDEX IF NOT EXISTS idx_failures_at   ON failures(at DESC);
CREATE INDEX IF NOT EXISTS idx_failures_act  ON failures(activity);

CREATE TABLE IF NOT EXISTS logs (
    id      BIGSERIAL   PRIMARY KEY,
    time    TIMESTAMPTZ NOT NULL,
    level   TEXT        NOT NULL,
    service TEXT        NOT NULL,
    bot_id  TEXT        NOT NULL,
    task_id TEXT,
    msg     TEXT        NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_logs_bot_task ON logs(bot_id, task_id);
CREATE INDEX IF NOT EXISTS idx_logs_time     ON logs(time DESC);
"""


async def init_schema(pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(SCHEMA_SQL)
