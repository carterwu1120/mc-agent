from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SubmitTaskRequest(BaseModel):
    goal: str = Field(min_length=1)
    commands: list[str] = Field(default_factory=list)
    interrupt: bool = False
    task_id: str | None = None

    @field_validator("commands")
    @classmethod
    def _validate_commands(cls, value: list[str]) -> list[str]:
        return [item.strip() for item in value if item and item.strip()]


class SubmitTaskResponse(BaseModel):
    task_id: str
    bot_id: str
    status: Literal["queued", "already_queued"]


class AbortResponse(BaseModel):
    bot_id: str
    ok: bool


class BotSummaryResponse(BaseModel):
    bot_id: str
    name: str
    online: bool
    activity: str | None = None
    position: dict[str, Any] | None = None
    health: int | float | None = None
    food: int | float | None = None
    current_task: str | None = None
    queued_count: int = 0
    last_seen: str | None = None
    source: str | None = None


class BotStateResponse(BaseModel):
    bot_id: str
    name: str
    online: bool
    last_seen: str | None = None
    status: dict[str, Any]
    current_task: dict[str, Any] | None = None
    interrupted_tasks: list[dict[str, Any]]
    equipment: dict[str, Any]
    inventory: list[dict[str, Any]]
    inventory_slots: dict[str, Any]
    chests: list[dict[str, Any]]
    recent_events: list[dict[str, Any]]
    recent_failures: list[dict[str, Any]]
    coordinator: dict[str, Any]


class BotRuntimeTasksResponse(BaseModel):
    bot_id: str
    online: bool
    last_seen: str | None = None
    running: list[dict[str, Any]]
    queued: list[dict[str, Any]]
    interrupt: dict[str, Any] | None = None
    abort_flag: bool = False


class PublicTaskLookupResponse(BaseModel):
    task_id: str
    bot_id: str | None = None
    goal: str | None = None
    commands: list[str] | None = None
    status: str
    source: Literal["runtime", "history"]


class SuccessRateResponse(BaseModel):
    since_hours: int
    task_success_rate: float | None = None
    goal_success_rate: float | None = None
    tasks: dict[str, int]


class StuckCountResponse(BaseModel):
    since_hours: int
    total: int
    by_reason: dict[str, int]
    by_activity: dict[str, int]


class LlmLatencyResponse(BaseModel):
    since_hours: int
    count: int
    avg_ms: float | None = None
    p95_ms: float | None = None
    by_skill: dict[str, dict[str, Any]]
