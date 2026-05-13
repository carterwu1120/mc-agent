import sqlite3

from fastapi.testclient import TestClient

from agent import history_db
from backend.main import create_app
from backend.services.coordinator_client import CoordinatorNotFoundError


class FakeCoordinatorClient:
    def __init__(self):
        self.submissions = []
        self.duplicate_ids = {"dup-task"}
        self.bots = [
            {
                "bot_id": "bot0",
                "registered": True,
                "online": True,
                "last_seen": "2026-01-01T00:00:00Z",
                "queued_count": 1,
                "running_task_ids": ["runtime-task"],
                "interrupt_task_id": None,
                "abort_flag": False,
            }
        ]
        self.bot_tasks = {
            "bot0": {
                "bot_id": "bot0",
                "online": True,
                "last_seen": "2026-01-01T00:00:00Z",
                "running": [
                    {
                        "task_id": "runtime-task",
                        "bot_id": "bot0",
                        "goal": "mine iron 8",
                        "commands": ["mine iron 8"],
                        "status": "running",
                        "interrupt": False,
                    }
                ],
                "queued": [],
                "interrupt": None,
                "abort_flag": False,
            }
        }
        self.runtime_tasks = {
            "runtime-task": {
                "task_id": "runtime-task",
                "bot_id": "bot0",
                "goal": "mine iron 8",
                "commands": ["mine iron 8"],
                "status": "running",
                "interrupt": False,
            }
        }

    async def submit_task(self, bot_id: str, **payload):
        if bot_id != "bot0":
            raise CoordinatorNotFoundError("bot not registered")
        self.submissions.append((bot_id, payload))
        if payload["task_id"] in self.duplicate_ids:
            return {"ok": True, "status": "already_queued"}
        return {"ok": True, "task_id": payload["task_id"]}

    async def get_bots(self):
        return list(self.bots)

    async def get_bot_tasks(self, bot_id: str):
        if bot_id not in self.bot_tasks:
            raise CoordinatorNotFoundError("bot not registered")
        return self.bot_tasks[bot_id]

    async def get_task(self, task_id: str):
        return self.runtime_tasks.get(task_id)


def _write_bot_files(tmp_path):
    bot_dir = tmp_path / "bot0"
    bot_dir.mkdir()
    (bot_dir / "live_state.json").write_text(
        """
        {
          "name": "Agent0",
          "ws_connected": true,
          "updated_at": "2026-01-01T00:00:00Z",
          "activity": "mining",
          "pos": {"x": 1, "y": 64, "z": 2},
          "health": 20,
          "food": 18,
          "inventory": [{"name": "iron_ore", "count": 3}],
          "equipment": {"hand": "stone_pickaxe"},
          "inventory_slots": {"used": 5}
        }
        """.strip(),
        encoding="utf-8",
    )
    (bot_dir / "task.json").write_text(
        """
        {
          "id": "runtime-task",
          "goal": "mine iron 8",
          "final_goal": "mine iron 8",
          "commands": ["mine iron 8"],
          "steps": [{"cmd": "mine iron 8", "status": "running"}],
          "currentStep": 0,
          "status": "running",
          "createdAt": "2026-01-01T00:00:00",
          "source": "coordinator",
          "interruptedTasks": [],
          "recentEvents": [{"type": "task_started", "at": "2026-01-01T00:00:01"}],
          "recentFailures": []
        }
        """.strip(),
        encoding="utf-8",
    )


def _write_history(tmp_path):
    bot_dir = tmp_path / "bot0"
    history_db.init(str(bot_dir))
    history_db.archive_task(
        {
            "id": "hist-task",
            "goal": "mine diamond 3",
            "final_goal": "mine diamond 3",
            "commands": ["mine diamond 3"],
            "steps": [{"cmd": "mine diamond 3", "status": "done"}],
            "status": "done",
            "goalVerified": True,
            "interruptedBy": None,
            "createdAt": "2099-01-01T00:00:00",
        }
    )
    history_db.write_failure(
        {
            "goal": "mine diamond 3",
            "command": "mine diamond 3",
            "step": 0,
            "reason": "no_tools",
            "activity": "mining",
            "at": "2099-01-01T00:00:00+00:00",
        },
        task_id="hist-task",
    )
    history_db.write_event(
        {
            "type": "llm_call",
            "details": {"skill": "planner", "latency_ms": 1250},
            "at": "2099-01-01T00:00:00+00:00",
        },
        task_id="hist-task",
    )
    if history_db._db_conn:
        history_db._db_conn.close()
        history_db._db_conn = None


def _write_legacy_history(tmp_path):
    db_path = tmp_path / "bot0" / "task_history.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY,
            goal TEXT NOT NULL,
            final_goal TEXT,
            commands TEXT NOT NULL,
            steps TEXT NOT NULL,
            status TEXT NOT NULL,
            interrupted_by TEXT,
            created_at TEXT NOT NULL,
            finished_at TEXT NOT NULL
        );
        CREATE TABLE failures (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            goal TEXT,
            command TEXT,
            step INTEGER,
            reason TEXT NOT NULL,
            activity TEXT,
            at TEXT NOT NULL
        );
        CREATE TABLE events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id TEXT,
            event_type TEXT NOT NULL,
            goal TEXT,
            to_goal TEXT,
            reason TEXT,
            command TEXT,
            step INTEGER,
            details TEXT,
            at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO tasks (id, goal, final_goal, commands, steps, status, interrupted_by, created_at, finished_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "legacy-task",
            "mine coal 3",
            "mine coal 3",
            "[\"mine coal 3\"]",
            "[{\"cmd\": \"mine coal 3\", \"status\": \"done\"}]",
            "done",
            None,
            "2099-01-01T00:00:00",
            "2099-01-01T00:00:01",
        ),
    )
    conn.execute(
        "INSERT INTO failures (task_id, goal, command, step, reason, activity, at) VALUES (?,?,?,?,?,?,?)",
        ("legacy-task", "mine coal 3", "mine coal 3", 0, "legacy_reason", "mining", "2099-01-01T00:00:00+00:00"),
    )
    conn.execute(
        "INSERT INTO events (task_id, event_type, goal, to_goal, reason, command, step, details, at) VALUES (?,?,?,?,?,?,?,?,?)",
        ("legacy-task", "llm_call", "mine coal 3", None, None, None, None, "{\"skill\": \"planner\", \"latency_ms\": 500}", "2099-01-01T00:00:00+00:00"),
    )
    conn.commit()
    conn.close()


def _make_client(tmp_path):
    _write_bot_files(tmp_path)
    _write_history(tmp_path)
    app = create_app(coordinator_client=FakeCoordinatorClient(), data_root=tmp_path)
    return TestClient(app)


def test_post_tasks_accepts_valid_payload(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post(
        "/bots/bot0/tasks",
        json={"goal": "mine iron 8", "commands": ["equip pickaxe", "mine iron 8"]},
    )
    assert resp.status_code == 202
    assert resp.json()["bot_id"] == "bot0"
    assert resp.json()["status"] == "queued"


def test_post_tasks_rejects_invalid_body(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post("/bots/bot0/tasks", json={"goal": "", "commands": []})
    assert resp.status_code == 422


def test_post_tasks_duplicate_id_returns_200(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post(
        "/bots/bot0/tasks",
        json={"goal": "mine iron 8", "commands": ["mine iron 8"], "task_id": "dup-task"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_queued"


def test_post_tasks_unknown_bot_returns_404(tmp_path):
    client = _make_client(tmp_path)
    resp = client.post(
        "/bots/bot9/tasks",
        json={"goal": "mine iron 8", "commands": ["mine iron 8"]},
    )
    assert resp.status_code == 404


def test_get_bots_and_state_use_shared_snapshot_data(tmp_path):
    client = _make_client(tmp_path)

    bots_resp = client.get("/bots")
    assert bots_resp.status_code == 200
    bots = bots_resp.json()
    assert bots[0]["bot_id"] == "bot0"
    assert bots[0]["current_task"] == "mine iron 8"
    assert bots[0]["source"] == "coordinator"

    state_resp = client.get("/bots/bot0/state")
    assert state_resp.status_code == 200
    state = state_resp.json()
    assert state["bot_id"] == "bot0"
    assert state["status"]["activity"] == "mining"
    assert state["current_task"]["id"] == "runtime-task"
    assert state["coordinator"]["running_task_ids"] == ["runtime-task"]


def test_get_runtime_and_history_tasks(tmp_path):
    client = _make_client(tmp_path)

    runtime_resp = client.get("/tasks/runtime-task")
    assert runtime_resp.status_code == 200
    assert runtime_resp.json()["source"] == "runtime"

    history_resp = client.get("/tasks/hist-task")
    assert history_resp.status_code == 200
    assert history_resp.json()["source"] == "history"
    assert history_resp.json()["commands"] == ["mine diamond 3"]

    missing_resp = client.get("/tasks/missing-task")
    assert missing_resp.status_code == 404


def test_metrics_endpoints_aggregate_history(tmp_path):
    client = _make_client(tmp_path)

    success_resp = client.get("/metrics/success-rate?hours=48")
    assert success_resp.status_code == 200
    assert success_resp.json()["tasks"]["done"] == 1
    assert success_resp.json()["goal_success_rate"] == 100.0

    stuck_resp = client.get("/metrics/stuck-count?hours=48")
    assert stuck_resp.status_code == 200
    assert stuck_resp.json()["by_reason"]["no_tools"] == 1

    latency_resp = client.get("/metrics/llm-latency?hours=48")
    assert latency_resp.status_code == 200
    assert latency_resp.json()["count"] == 1
    assert latency_resp.json()["avg_ms"] == 1250.0


def test_metrics_endpoints_work_with_legacy_schema(tmp_path):
    _write_bot_files(tmp_path)
    _write_legacy_history(tmp_path)
    client = TestClient(create_app(coordinator_client=FakeCoordinatorClient(), data_root=tmp_path))

    success_resp = client.get("/metrics/success-rate?hours=48")
    assert success_resp.status_code == 200
    assert success_resp.json()["tasks"]["done"] == 1
    assert success_resp.json()["goal_success_rate"] == 0.0

    stuck_resp = client.get("/metrics/stuck-count?hours=48")
    assert stuck_resp.status_code == 200
    assert stuck_resp.json()["by_reason"]["legacy_reason"] == 1

    latency_resp = client.get("/metrics/llm-latency?hours=48")
    assert latency_resp.status_code == 200
    assert latency_resp.json()["count"] == 1


def test_get_bots_marks_stale_snapshot_offline(tmp_path):
    _write_bot_files(tmp_path)
    _write_history(tmp_path)
    stale_path = tmp_path / "bot1"
    stale_path.mkdir()
    (stale_path / "live_state.json").write_text(
        """
        {
          "name": "Agent1",
          "ws_connected": true,
          "updated_at": "2026-01-01T00:00:00Z",
          "activity": "idle",
          "pos": {"x": 0, "y": 64, "z": 0},
          "health": 20,
          "food": 20
        }
        """.strip(),
        encoding="utf-8",
    )
    client = TestClient(create_app(coordinator_client=FakeCoordinatorClient(), data_root=tmp_path))

    resp = client.get("/bots")
    assert resp.status_code == 200
    bots = {item["bot_id"]: item for item in resp.json()}
    assert bots["bot1"]["online"] is False
