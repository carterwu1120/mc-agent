from __future__ import annotations

import os

import aiohttp


class CoordinatorClientError(Exception):
    pass


class CoordinatorNotFoundError(CoordinatorClientError):
    pass


class CoordinatorClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or os.environ.get("COORDINATOR_URL") or "http://agent0:3010").rstrip("/")

    async def submit_task(
        self,
        bot_id: str,
        *,
        task_id: str,
        goal: str,
        commands: list[str],
        interrupt: bool,
    ) -> dict:
        return await self._request(
            "POST",
            f"/bots/{bot_id}/tasks",
            json={
                "task_id": task_id,
                "goal": goal,
                "commands": commands,
                "interrupt": interrupt,
            },
        )

    async def get_bots(self) -> list[dict]:
        payload = await self._request("GET", "/internal/bots")
        return payload.get("bots") or []

    async def get_bot_tasks(self, bot_id: str) -> dict:
        return await self._request("GET", f"/internal/bots/{bot_id}/tasks")

    async def get_task(self, task_id: str) -> dict | None:
        try:
            payload = await self._request("GET", f"/internal/tasks/{task_id}")
        except CoordinatorNotFoundError:
            return None
        return payload.get("task")

    async def abort(self, bot_id: str) -> dict:
        return await self._request("POST", f"/bots/{bot_id}/abort")

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, **kwargs) as response:
                    if response.status == 404:
                        raise CoordinatorNotFoundError(await response.text())
                    if response.status >= 400:
                        raise CoordinatorClientError(f"{response.status}: {await response.text()}")
                    return await response.json()
        except CoordinatorClientError:
            raise
        except Exception as exc:
            raise CoordinatorClientError(str(exc)) from exc
