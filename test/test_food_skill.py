from agent.skills.food import _build_food_plan


def test_food_plan_uses_existing_raw_food_with_dynamic_target():
    state = {
        "food": 12,
        "activity": "idle",
        "inventory": [{"name": "beef", "count": 2}],
    }

    commands, target = _build_food_plan(state)

    assert target == 2
    assert commands == ["getfood count 2"]


def test_food_plan_acquires_raw_food_before_getfood():
    state = {
        "food": 8,
        "activity": "mining",
        "inventory": [],
        "entities": [],
        "nearby": {"water": False},
    }

    commands, target = _build_food_plan(state)

    assert target == 4
    assert commands == [
        "explore animals",
        "hunt count 4",
        "getfood count 4",
    ]


def test_food_plan_uses_rod_only_when_water_is_nearby():
    state = {
        "food": 12,
        "activity": "idle",
        "inventory": [{"name": "fishing_rod", "count": 1}],
        "nearby": {"water": True},
    }

    commands, target = _build_food_plan(state)

    assert target == 2
    assert commands == ["fish catches 2", "getfood count 2"]
