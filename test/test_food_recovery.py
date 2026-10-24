from agent import food_recovery


def _state(*, food=9, inventory=None, animals=False, water=False, safe_surface=False):
    entities = []
    nearby = {
        "animals": animals,
        "water": water,
        "safe_surface": safe_surface,
    }
    return {
        "food": food,
        "inventory": inventory or [],
        "entities": entities,
        "nearby": nearby,
        "equipment": {},
    }


def test_unchanged_or_worse_hunger_does_not_release_food_wait():
    baseline = food_recovery.snapshot(_state(food=9))

    assert food_recovery.classify_change(baseline, _state(food=9)) is None
    assert food_recovery.classify_change(baseline, _state(food=8)) is None


def test_resolved_hunger_resumes_the_interrupted_task():
    baseline = food_recovery.snapshot(_state(food=9))

    result = food_recovery.classify_change(baseline, _state(food=12))

    assert result["action"] == "resume_task"


def test_new_animal_opportunity_retries_food_once():
    baseline = food_recovery.snapshot(_state(food=9, animals=False))

    result = food_recovery.classify_change(
        baseline,
        _state(food=9, animals=True),
    )

    assert result["action"] == "retry_food"


def test_new_raw_food_retries_processing_without_waiting_for_position_change():
    baseline = food_recovery.snapshot(_state(food=9))
    raw_food = [{"name": "beef", "count": 2}]

    result = food_recovery.classify_change(
        baseline,
        _state(food=9, inventory=raw_food),
    )

    assert result["action"] == "retry_food"


def test_new_fishing_opportunity_requires_both_rod_and_water():
    baseline = food_recovery.snapshot(_state(food=9))
    rod = [{"name": "fishing_rod", "count": 1}]

    assert food_recovery.classify_change(
        baseline,
        _state(food=9, inventory=rod, water=False),
    ) is None
    result = food_recovery.classify_change(
        baseline,
        _state(food=9, inventory=rod, water=True),
    )
    assert result["action"] == "retry_food"


def test_food_task_detection_is_semantic_not_position_based():
    assert food_recovery.is_food_recovery_task({
        "goal": "原任務",
        "interruptedBy": "food_low",
        "steps": [],
    })
    assert food_recovery.is_food_recovery_task({
        "goal": "補充食物",
        "steps": [{"cmd": "explore animals"}],
    })
    assert not food_recovery.is_food_recovery_task({
        "goal": "取得木材",
        "steps": [{"cmd": "hunt count 2"}, {"cmd": "chop logs 5"}],
    })
