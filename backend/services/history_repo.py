from __future__ import annotations

from agent.db import reader as db_reader


class HistoryRepository:
    async def find_task(self, task_id: str) -> dict | None:
        return await db_reader.find_task(task_id)

    async def metrics(self, since_hours: int = 24, bot_id: str | None = None) -> dict:
        return await db_reader.query_metrics(since_hours=since_hours, bot_id=bot_id)

    async def goal_completion(self, since_hours: int = 24, bot_id: str | None = None) -> dict:
        return await db_reader.query_goal_completion(since_hours=since_hours, bot_id=bot_id)
