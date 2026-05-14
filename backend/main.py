from __future__ import annotations

import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware

from agent.db import pool as db_pool
from agent.db.schema import init_schema

from backend.models import (
    AbortResponse,
    BotRuntimeTasksResponse,
    BotStateResponse,
    BotSummaryResponse,
    GoalCompletionResponse,
    LlmLatencyResponse,
    PublicTaskLookupResponse,
    ReplanMetricsResponse,
    StepDurationResponse,
    StuckCountResponse,
    StuckDetailResponse,
    SubmitTaskRequest,
    SubmitTaskResponse,
    SuccessRateResponse,
)
from backend.services.coordinator_client import (
    CoordinatorClient,
    CoordinatorClientError,
    CoordinatorNotFoundError,
)
from backend.services.history_repo import HistoryRepository
from backend.services.state_repo import StateRepository


@asynccontextmanager
async def _lifespan(app: FastAPI):
    pool = await db_pool.init_pool()  # raises clearly if DATABASE_URL is not set
    await init_schema(pool)
    yield
    await db_pool.close_pool()


def create_app(
    *,
    coordinator_client: CoordinatorClient | None = None,
    data_root: str | Path | None = None,
) -> FastAPI:
    app = FastAPI(title="MC Agent Backend", version="0.1.0", lifespan=_lifespan)
    resolved_data_root = Path(data_root or os.environ.get("AGENT_DATA_ROOT") or "/app/agent/data")
    allowed_origins = [
        "http://localhost:3002",
        "http://127.0.0.1:3002",
    ]
    extra_origins = os.environ.get("BACKEND_CORS_ORIGINS", "").strip()
    if extra_origins:
        allowed_origins.extend(
            origin.strip() for origin in extra_origins.split(",") if origin.strip()
        )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    app.state.coordinator_client = coordinator_client or CoordinatorClient()
    app.state.state_repo = StateRepository(resolved_data_root)
    app.state.history_repo = HistoryRepository()

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "backend"}

    @app.get("/ready")
    async def ready():
        try:
            await app.state.coordinator_client.get_bots()
        except CoordinatorClientError as exc:
            raise HTTPException(
                status_code=503,
                detail={"ok": False, "service": "backend", "coordinator": False, "reason": str(exc)},
            ) from exc
        return {"ok": True, "service": "backend", "coordinator": True}

    @app.get("/bots", response_model=list[BotSummaryResponse])
    async def list_bots():
        try:
            coordinator_bots = await app.state.coordinator_client.get_bots()
        except CoordinatorClientError as exc:
            raise HTTPException(status_code=502, detail=f"coordinator unavailable: {exc}") from exc
        bot_views = app.state.state_repo.list_bots(coordinator_bots)
        return [app.state.state_repo.summarize(item) for item in bot_views]

    @app.get("/bots/{bot_id}/state", response_model=BotStateResponse)
    async def get_bot_state(bot_id: str):
        try:
            coordinator_bots = await app.state.coordinator_client.get_bots()
        except CoordinatorClientError as exc:
            raise HTTPException(status_code=502, detail=f"coordinator unavailable: {exc}") from exc
        coordinator_map = {item["bot_id"]: item for item in coordinator_bots}
        if bot_id not in coordinator_map and not (app.state.state_repo.data_root / bot_id).exists():
            raise HTTPException(status_code=404, detail="bot not found")
        try:
            coordinator_detail = await app.state.coordinator_client.get_bot_tasks(bot_id)
        except CoordinatorNotFoundError:
            coordinator_detail = None
        except CoordinatorClientError as exc:
            raise HTTPException(status_code=502, detail=f"coordinator unavailable: {exc}") from exc
        bot_view = app.state.state_repo.get_bot_state(
            bot_id,
            coordinator_state=coordinator_map.get(bot_id),
            coordinator_detail=coordinator_detail,
        )
        return {
            "bot_id": bot_view["id"],
            "name": bot_view["name"],
            "online": bot_view["online"],
            "last_seen": bot_view["last_seen"],
            "status": bot_view["status"],
            "current_task": bot_view["current_task"],
            "interrupted_tasks": bot_view["interrupted_tasks"],
            "equipment": bot_view["equipment"],
            "inventory": bot_view["inventory"],
            "inventory_slots": bot_view["inventory_slots"],
            "chests": bot_view["chests"],
            "recent_events": bot_view["recent_events"],
            "recent_failures": bot_view["recent_failures"],
            "coordinator": {
                "online": coordinator_detail["online"] if coordinator_detail else bot_view["online"],
                "queued_count": len((coordinator_detail or {}).get("queued") or []),
                "running_task_ids": [item["task_id"] for item in (coordinator_detail or {}).get("running") or []],
                "interrupt_task_id": ((coordinator_detail or {}).get("interrupt") or {}).get("task_id"),
                "abort_flag": (coordinator_detail or {}).get("abort_flag", False),
            },
        }

    @app.get("/bots/{bot_id}/tasks", response_model=BotRuntimeTasksResponse)
    async def get_bot_tasks(bot_id: str):
        try:
            payload = await app.state.coordinator_client.get_bot_tasks(bot_id)
        except CoordinatorNotFoundError as exc:
            raise HTTPException(status_code=404, detail="bot not registered") from exc
        except CoordinatorClientError as exc:
            raise HTTPException(status_code=502, detail=f"coordinator unavailable: {exc}") from exc
        return payload

    @app.post("/bots/{bot_id}/tasks", response_model=SubmitTaskResponse, status_code=202)
    async def submit_task(bot_id: str, body: SubmitTaskRequest, response: Response):
        task_id = body.task_id or uuid.uuid4().hex[:12]
        try:
            payload = await app.state.coordinator_client.submit_task(
                bot_id,
                task_id=task_id,
                goal=body.goal,
                commands=body.commands,
                interrupt=body.interrupt,
            )
        except CoordinatorNotFoundError as exc:
            raise HTTPException(status_code=404, detail="bot not registered") from exc
        except CoordinatorClientError as exc:
            raise HTTPException(status_code=502, detail=f"coordinator unavailable: {exc}") from exc

        status = payload.get("status") or "queued"
        payload_out = {"task_id": task_id, "bot_id": bot_id, "status": status}
        if status == "already_queued":
            response.status_code = 200
            return SubmitTaskResponse(**payload_out)
        return SubmitTaskResponse(**payload_out)

    @app.post("/bots/{bot_id}/abort", response_model=AbortResponse)
    async def abort_bot(bot_id: str):
        try:
            await app.state.coordinator_client.abort(bot_id)
        except CoordinatorNotFoundError as exc:
            raise HTTPException(status_code=404, detail="bot not registered") from exc
        except CoordinatorClientError as exc:
            raise HTTPException(status_code=502, detail=f"coordinator unavailable: {exc}") from exc
        return {"bot_id": bot_id, "ok": True}

    @app.post("/bots/{bot_id}/resume", response_model=SubmitTaskResponse, status_code=202)
    async def resume_bot(bot_id: str):
        task_id = uuid.uuid4().hex[:12]
        try:
            payload = await app.state.coordinator_client.submit_task(
                bot_id,
                task_id=task_id,
                goal="resume",
                commands=["resumetask"],
                interrupt=True,
            )
        except CoordinatorNotFoundError as exc:
            raise HTTPException(status_code=404, detail="bot not registered") from exc
        except CoordinatorClientError as exc:
            raise HTTPException(status_code=502, detail=f"coordinator unavailable: {exc}") from exc
        return SubmitTaskResponse(task_id=task_id, bot_id=bot_id, status=payload.get("status", "queued"))

    @app.get("/tasks/{task_id}", response_model=PublicTaskLookupResponse)
    async def get_task(task_id: str):
        try:
            runtime_task = await app.state.coordinator_client.get_task(task_id)
        except CoordinatorClientError as exc:
            raise HTTPException(status_code=502, detail=f"coordinator unavailable: {exc}") from exc
        if runtime_task:
            return {
                "task_id": runtime_task.get("task_id"),
                "bot_id": runtime_task.get("bot_id"),
                "goal": runtime_task.get("goal"),
                "commands": runtime_task.get("commands"),
                "status": runtime_task.get("status") or "queued",
                "source": "runtime",
            }

        history_task = await app.state.history_repo.find_task(task_id)
        if history_task:
            return {
                "task_id": history_task.get("id"),
                "bot_id": history_task.get("bot_id"),
                "goal": history_task.get("goal"),
                "commands": history_task.get("commands"),
                "status": history_task.get("status") or "unknown",
                "source": "history",
            }
        raise HTTPException(status_code=404, detail="task not found")

    @app.get("/metrics/success-rate", response_model=SuccessRateResponse)
    async def get_success_rate(
        hours: int = Query(24, ge=1, le=168),
        bot_id: str | None = Query(None),
    ):
        metrics = await app.state.history_repo.metrics(since_hours=hours, bot_id=bot_id)
        return {
            "since_hours": hours,
            "task_success_rate": metrics.get("task_success_rate"),
            "goal_success_rate": metrics.get("goal_success_rate"),
            "tasks": metrics.get("tasks") or {},
        }

    @app.get("/metrics/stuck-count", response_model=StuckCountResponse)
    async def get_stuck_count(
        hours: int = Query(24, ge=1, le=168),
        bot_id: str | None = Query(None),
    ):
        metrics = await app.state.history_repo.metrics(since_hours=hours, bot_id=bot_id)
        by_reason = metrics.get("stuck_by_reason") or {}
        return {
            "since_hours": hours,
            "total": sum(by_reason.values()),
            "by_reason": by_reason,
            "by_activity": metrics.get("stuck_by_activity") or {},
        }

    @app.get("/metrics/llm-latency", response_model=LlmLatencyResponse)
    async def get_llm_latency(
        hours: int = Query(24, ge=1, le=168),
        bot_id: str | None = Query(None),
    ):
        metrics = await app.state.history_repo.metrics(since_hours=hours, bot_id=bot_id)
        llm_latency = metrics.get("llm_latency") or {}
        return {
            "since_hours": hours,
            "count": llm_latency.get("count", 0),
            "avg_ms": llm_latency.get("avg_ms"),
            "p95_ms": llm_latency.get("p95_ms"),
            "by_skill": llm_latency.get("by_skill") or {},
        }

    @app.get("/metrics/goal-completion", response_model=GoalCompletionResponse)
    async def get_goal_completion(
        hours: int = Query(24, ge=1, le=168),
        bot_id: str | None = Query(None),
    ):
        data = await app.state.history_repo.goal_completion(since_hours=hours, bot_id=bot_id)
        return {
            "since_hours": hours,
            "tasks_with_goal": data.get("tasks_with_goal", 0),
            "fully_met": data.get("fully_met", 0),
            "partial": data.get("partial", 0),
            "fully_met_rate": data.get("fully_met_rate"),
            "avg_completion_pct": data.get("avg_completion_pct"),
        }

    @app.get("/metrics/replan-count", response_model=ReplanMetricsResponse)
    async def get_replan_count(
        hours: int = Query(24, ge=1, le=168),
        bot_id: str | None = Query(None),
    ):
        data = await app.state.history_repo.replan_metrics(since_hours=hours, bot_id=bot_id)
        return {
            "since_hours": hours,
            "total": data.get("total", 0),
            "by_reason": data.get("by_reason") or {},
            "by_activity": data.get("by_activity") or {},
            "by_path": data.get("by_path") or {},
        }

    @app.get("/metrics/step-duration", response_model=StepDurationResponse)
    async def get_step_duration(
        hours: int = Query(24, ge=1, le=168),
        bot_id: str | None = Query(None),
    ):
        data = await app.state.history_repo.step_duration_metrics(since_hours=hours, bot_id=bot_id)
        return {
            "since_hours": hours,
            "by_command": data.get("by_command") or {},
            "timeouts": data.get("timeouts") or {},
        }

    @app.get("/metrics/stuck-detail", response_model=StuckDetailResponse)
    async def get_stuck_detail(
        hours: int = Query(24, ge=1, le=168),
        bot_id: str | None = Query(None),
    ):
        data = await app.state.history_repo.stuck_detail_metrics(since_hours=hours, bot_id=bot_id)
        return {
            "since_hours": hours,
            "total": data.get("total", 0),
            "watchdog_triggered": data.get("watchdog_triggered", 0),
            "by_activity_reason": data.get("by_activity_reason") or [],
        }

    return app


app = create_app()
