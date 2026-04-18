from agent.skills.self_task import _normalize_result, _is_valid_command_result, _is_valid_plan_result


# ── _normalize_result ─────────────────────────────────────────────────────────

def test_normalize_getfood_adds_goal():
    assert _normalize_result({"command": "getfood"})["goal"] == {"count": 8}


def test_normalize_chop_adds_goal():
    assert _normalize_result({"command": "chop"})["goal"] == {"logs": 8}


def test_normalize_mine_adds_count():
    result = _normalize_result({"command": "mine", "args": ["diamond"]})
    assert result["args"] == ["diamond", "8"]


def test_normalize_passthrough():
    r = {"command": "equip"}
    assert _normalize_result(r) == {"command": "equip"}


# ── _is_valid_command_result ──────────────────────────────────────────────────

def test_valid_getfood():
    assert _is_valid_command_result({"command": "getfood", "goal": {"count": 8}})


def test_invalid_getfood_missing_goal():
    assert not _is_valid_command_result({"command": "getfood"})


def test_valid_mine():
    assert _is_valid_command_result({"command": "mine", "args": ["diamond", "5"]})


def test_invalid_unknown_command():
    assert not _is_valid_command_result({"command": "fish"})


# ── _is_valid_plan_result ─────────────────────────────────────────────────────

def test_valid_plan():
    assert _is_valid_plan_result({"action": "plan", "commands": ["mine diamond 5", "equip"]})


def test_invalid_plan_empty_commands():
    assert not _is_valid_plan_result({"action": "plan", "commands": []})


def test_invalid_plan_disallowed_command():
    assert not _is_valid_plan_result({"action": "plan", "commands": ["fish 10"]})


def test_invalid_plan_wrong_action():
    assert not _is_valid_plan_result({"action": "drop", "commands": ["mine stone 5"]})
