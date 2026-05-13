from __future__ import annotations

from pathlib import Path

from agent.state_reader import build_bot_view, iter_bot_ids, load_bot_bundle, summarize_bot_view


class StateRepository:
    def __init__(self, data_root: str | Path):
        self.data_root = Path(data_root)

    def list_bots(self, coordinator_bots: list[dict]) -> list[dict]:
        bot_views = []
        coordinator_by_id = {item["bot_id"]: item for item in coordinator_bots}
        bot_ids = set(iter_bot_ids(self.data_root)) | set(coordinator_by_id)
        for bot_id in sorted(bot_ids):
            bot_views.append(self.get_bot_state(bot_id, coordinator_by_id.get(bot_id)))
        return bot_views

    def get_bot_state(self, bot_id: str, coordinator_state: dict | None = None, coordinator_detail: dict | None = None) -> dict:
        bundle = load_bot_bundle(bot_id, self.data_root)
        return build_bot_view(
            bot_id,
            snapshot=bundle["snapshot"],
            task=bundle["task"],
            chests=bundle["chests"],
            internal_state=coordinator_state,
            internal_detail=coordinator_detail,
            expose_internal=False,
        )

    def summarize(self, bot_view: dict) -> dict:
        return summarize_bot_view(bot_view)
