from __future__ import annotations

from pathlib import Path

from agent import history_reader


class HistoryRepository:
    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root)

    def find_task(self, task_id: str) -> dict | None:
        return history_reader.find_task(task_id, data_root=self.data_root)

    def metrics(self, since_hours: int = 24) -> dict:
        return history_reader.query_metrics(data_root=self.data_root, since_hours=since_hours)
