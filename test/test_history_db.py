import pytest
from agent import history_db


@pytest.fixture(autouse=True)
def tmp_db(tmp_path):
    history_db.init(str(tmp_path))
    yield
    if history_db._db_conn:
        history_db._db_conn.close()
        history_db._db_conn = None


_TASK = {
    "id": "abc123",
    "goal": "mine diamond 5",
    "final_goal": "get diamonds",
    "commands": ["mine diamond 5"],
    "steps": [{"cmd": "mine diamond 5", "status": "done"}],
    "status": "done",
    "interruptedBy": None,
    "createdAt": "2026-01-01T00:00:00",
}


# ── tasks ─────────────────────────────────────────────────────────────────────

def test_archive_and_query_task():
    history_db.archive_task(_TASK)
    rows = history_db.query_history(limit=10)
    assert len(rows) == 1
    assert rows[0]["id"] == "abc123"
    assert rows[0]["status"] == "done"


def test_archive_is_idempotent():
    history_db.archive_task(_TASK)
    history_db.archive_task({**_TASK, "status": "failed"})
    rows = history_db.query_history()
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"  # INSERT OR REPLACE overwrites


def test_query_history_filter_by_status():
    history_db.archive_task({**_TASK, "id": "t1", "status": "done"})
    history_db.archive_task({**_TASK, "id": "t2", "status": "done"})
    history_db.archive_task({**_TASK, "id": "t3", "status": "failed"})
    done = history_db.query_history(status="done")
    assert len(done) == 2
    assert all(r["status"] == "done" for r in done)


# ── failures ──────────────────────────────────────────────────────────────────

def test_write_and_query_failure():
    history_db.write_failure(
        {"goal": "mine diamond 5", "command": "mine diamond 5", "step": 0, "reason": "no_tools", "activity": "mining"},
        task_id="abc123",
    )
    rows = history_db.query_failures()
    assert len(rows) == 1
    assert rows[0]["reason"] == "no_tools"
    assert rows[0]["activity"] == "mining"


def test_query_failures_filter_by_activity():
    history_db.write_failure({"reason": "no_tools", "activity": "mining"}, task_id=None)
    history_db.write_failure({"reason": "stuck", "activity": "chopping"}, task_id=None)
    rows = history_db.query_failures(activity="mining")
    assert len(rows) == 1
    assert rows[0]["activity"] == "mining"


# ── events ────────────────────────────────────────────────────────────────────

def test_write_and_query_event():
    history_db.write_event({"type": "task_started", "goal": "mine diamond 5"}, task_id="abc123")
    rows = history_db.query_events()
    assert len(rows) == 1
    assert rows[0]["event_type"] == "task_started"


def test_query_events_filter_by_type():
    history_db.write_event({"type": "task_started"}, task_id=None)
    history_db.write_event({"type": "activity_stuck"}, task_id=None)
    rows = history_db.query_events(event_type="task_started")
    assert len(rows) == 1
    assert rows[0]["event_type"] == "task_started"
