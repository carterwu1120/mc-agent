from agent.handlers.food import _build_food_plan


def test_partial_raw_food_is_cooked_without_exploring():
    commands, target = _build_food_plan({'food': 4, 'inventory': [{'name': 'beef', 'count': 1}]})
    assert target == 4
    assert commands == ['getfood count 1']


def test_local_animals_still_allow_hunting():
    commands, _ = _build_food_plan({'food': 8, 'entities': [{'name': 'cow', 'type': 'mob', 'distance': 4}]})
    assert commands == ['hunt count 3', 'getfood count 3']


def test_food_plan_uses_existing_raw_food_with_dynamic_target():
    state = {
        "food": 12,
        "activity": "idle",
        "inventory": [{"name": "beef", "count": 2}],
    }

    commands, target = _build_food_plan(state)

    assert target == 2
    assert commands == ["getfood count 2"]


def test_food_plan_waits_without_local_source():
    state = {
        "food": 8,
        "activity": "mining",
        "inventory": [],
        "entities": [],
        "nearby": {"water": False},
    }

    commands, target = _build_food_plan(state)

    assert target == 4
    assert commands == []


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
