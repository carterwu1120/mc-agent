from __future__ import annotations

from collections import Counter
import json

RAW_FOOD_ITEMS = {
    "beef", "porkchop", "chicken", "mutton", "rabbit",
    "cod", "salmon", "potato",
}

COOKED_FOOD_ITEMS = {
    "bread", "apple", "golden_apple", "enchanted_golden_apple",
    "cooked_beef", "cooked_chicken", "cooked_porkchop", "cooked_mutton",
    "cooked_rabbit", "cooked_cod", "cooked_salmon", "baked_potato",
    "carrot", "beetroot", "melon_slice", "pumpkin_pie", "cookie",
    "mushroom_stew", "rabbit_stew", "suspicious_stew",
}

HOSTILE_MOBS = {
    "zombie", "skeleton", "creeper", "spider", "cave_spider", "witch",
    "slime", "magma_cube", "blaze", "wither_skeleton", "zombie_villager",
    "husk", "stray", "drowned", "phantom", "pillager", "vindicator",
    "evoker", "vex", "ravager", "endermite", "silverfish", "guardian",
    "elder_guardian",
}

ANIMAL_MOBS = {"cow", "pig", "chicken", "sheep", "rabbit"}
LOG_SUFFIX = "_log"
PLANK_SUFFIX = "_planks"
GOOD_WEAPON_PREFIXES = ("iron_", "diamond_", "netherite_")
GOOD_ARMOR_PREFIXES = ("iron_", "diamond_", "netherite_")


def _inventory_counts(state: dict) -> Counter:
    inventory = state.get("inventory", []) or []
    counter = Counter()
    for item in inventory:
        name = item.get("name")
        count = int(item.get("count", 0) or 0)
        if name:
            counter[name] += count
    return counter


def _sum_matching(counter: Counter, predicate) -> int:
    return sum(count for name, count in counter.items() if predicate(name))


def _list_matching(counter: Counter, predicate) -> list[str]:
    return sorted([name for name, count in counter.items() if count > 0 and predicate(name)])


def _tools(counter: Counter) -> dict:
    return {
        "pickaxe": _list_matching(counter, lambda n: n.endswith("_pickaxe")),
        "axe": _list_matching(counter, lambda n: n.endswith("_axe")),
        "sword": _list_matching(counter, lambda n: n.endswith("_sword")),
    }


def _resources(counter: Counter) -> dict:
    return {
        "food": {
            "cooked_total": _sum_matching(counter, lambda n: n in COOKED_FOOD_ITEMS),
            "raw_total": _sum_matching(counter, lambda n: n in RAW_FOOD_ITEMS),
        },
        "wood": {
            "logs": _sum_matching(counter, lambda n: n.endswith(LOG_SUFFIX)),
            "planks": _sum_matching(counter, lambda n: n.endswith(PLANK_SUFFIX)),
            "sticks": counter.get("stick", 0),
        },
        "materials": {
            "cobblestone": counter.get("cobblestone", 0),
            "coal": counter.get("coal", 0) + counter.get("charcoal", 0),
            "iron_ingot": counter.get("iron_ingot", 0),
            "raw_iron": counter.get("raw_iron", 0),
            "gold_ingot": counter.get("gold_ingot", 0),
            "raw_gold": counter.get("raw_gold", 0),
            "diamond": counter.get("diamond", 0),
        },
        "utility": {
            "crafting_table": counter.get("crafting_table", 0),
            "furnace": counter.get("furnace", 0),
            "shield": counter.get("shield", 0),
        },
        "tools": _tools(counter),
    }


def _item_name(item) -> str | None:
    """Extract item name from either a plain string or {name, durability_pct} dict."""
    if item is None:
        return None
    if isinstance(item, dict):
        return item.get("name")
    return item


def _item_durability_pct(item) -> int | None:
    """Return durability_pct if available, else None (meaning unknown/full)."""
    if isinstance(item, dict):
        return item.get("durability_pct")
    return None


def _is_broken(item) -> bool:
    pct = _item_durability_pct(item)
    return pct is not None and pct <= 0


def _has_good_weapon(counter: Counter, equipment: dict) -> bool:
    main_hand_item = (equipment or {}).get("main_hand")
    names = set(counter.keys())
    if not _is_broken(main_hand_item):
        name = _item_name(main_hand_item)
        if name:
            names.add(name)
    return any(name.endswith(("_sword", "_axe")) and name.startswith(GOOD_WEAPON_PREFIXES) for name in names)


def _has_good_armor(counter: Counter, equipment: dict) -> bool:
    pieces = set()
    armor = ((equipment or {}).get("armor") or {})
    for piece in armor.values():
        if not _is_broken(piece):
            name = _item_name(piece)
            if name:
                pieces.add(name)
    for name in counter:
        if any(name.endswith(suffix) for suffix in ("_helmet", "_chestplate", "_leggings", "_boots")):
            pieces.add(name)
    good_count = sum(1 for name in pieces if name.startswith(GOOD_ARMOR_PREFIXES))
    return good_count >= 2


def _low_durability_equipment(equipment: dict) -> list[str]:
    """Return list of equipped item names with durability <= 10%."""
    low = []
    main_hand = equipment.get("main_hand")
    pct = _item_durability_pct(main_hand)
    if pct is not None and pct <= 10:
        low.append(_item_name(main_hand))
    armor = (equipment.get("armor") or {})
    for piece in armor.values():
        pct = _item_durability_pct(piece)
        if pct is not None and pct <= 10:
            name = _item_name(piece)
            if name:
                low.append(name)
    return low


def _capabilities(resources: dict, equipment: dict) -> dict:
    planks = resources["wood"]["planks"]
    sticks = resources["wood"]["sticks"]
    cobblestone = resources["materials"]["cobblestone"]
    iron_ingot = resources["materials"]["iron_ingot"]
    diamond = resources["materials"]["diamond"]
    furnace = resources["utility"]["furnace"]
    crafting_table = resources["utility"]["crafting_table"]
    raw_food = resources["food"]["raw_total"]
    coal = resources["materials"]["coal"]

    return {
        "can_make_crafting_table": crafting_table > 0 or planks >= 4,
        "can_make_furnace": furnace > 0 or cobblestone >= 8,
        "can_make_pickaxe": sticks >= 2 and (planks >= 3 or cobblestone >= 3 or iron_ingot >= 3 or diamond >= 3),
        "can_make_sword": sticks >= 1 and (planks >= 2 or cobblestone >= 2 or iron_ingot >= 2 or diamond >= 2),
        "can_smelt_food": raw_food > 0 and coal > 0 and (furnace > 0 or cobblestone >= 8),
        "has_good_weapon": _has_good_weapon(Counter({
            **{name: 1 for name in resources["tools"]["sword"]},
            **{name: 1 for name in resources["tools"]["axe"]},
        }), equipment),
        "has_good_armor": _has_good_armor(Counter(), equipment),
        "low_durability_equipment": _low_durability_equipment(equipment),
    }


def _danger_score(state: dict) -> float:
    health = float(state.get("health", 20) or 20)
    food = float(state.get("food", 20) or 20)
    entities = state.get("entities", []) or []
    hostiles = [e for e in entities if (e.get("name") or "").lower() in HOSTILE_MOBS and (e.get("distance") or 999) <= 16]

    score = 0.0
    if hostiles:
        score += min(0.6, 0.2 * len(hostiles))
        nearest = min(float(e.get("distance", 999)) for e in hostiles)
        if nearest <= 4:
            score += 0.2
        elif nearest <= 8:
            score += 0.1
    if health < 10:
        score += 0.2
    elif health < 16:
        score += 0.1
    if food < 10:
        score += 0.1
    return round(min(score, 1.0), 2)


def _environment(state: dict) -> dict:
    entities = state.get("entities", []) or []
    near_animals = sorted({
        (e.get("name") or "").lower()
        for e in entities
        if (e.get("name") or "").lower() in ANIMAL_MOBS and (e.get("distance") or 999) <= 16
    })
    near_hostiles = sorted({
        (e.get("name") or "").lower()
        for e in entities
        if (e.get("name") or "").lower() in HOSTILE_MOBS and (e.get("distance") or 999) <= 16
    })
    time_of_day = state.get("timeOfDay")
    is_night = bool(time_of_day is not None and 13000 <= int(time_of_day) <= 23000)
    nearby = state.get("nearby") or {}
    chests = state.get("chests") or []

    return {
        "is_night": is_night,
        "danger_score": _danger_score(state),
        "near_water": nearby.get("water"),
        "near_trees": nearby.get("trees"),
        "near_stone": nearby.get("stone"),
        "near_animals": near_animals,
        "near_hostiles": near_hostiles,
        "home_set": bool(state.get("home")),
        "known_chests": len(chests),
    }


def summarize_state(state: dict, mode: str = "companion_survival") -> dict:
    counter = _inventory_counts(state)
    equipment = state.get("equipment") or {"main_hand": None, "off_hand": None, "armor": {}}
    resources = _resources(counter)
    return {
        "agent": {
            "mode": mode,
            "health": state.get("health"),
            "food": state.get("food"),
            "position": state.get("pos"),
            "dimension": state.get("dimension"),
            "time_of_day": state.get("timeOfDay"),
            "current_activity": state.get("activity"),
            "activity_stack": [entry.get("name") for entry in (state.get("stack") or [])],
            "busy": state.get("activity") not in (None, "idle"),
            "equipment": equipment,
        },
        "resources": resources,
        "capabilities": _capabilities(resources, equipment),
        "environment": _environment(state),
        "inventory_slots": state.get("inventory_slots") or {"used": len(state.get("inventory") or []), "total": 36, "free": 36 - len(state.get("inventory") or [])},
        "tasks": {
            "player_task": state.get("player_task"),
            "queued_tasks": state.get("queued_tasks", []),
        },
    }


def summary_json(state: dict, mode: str = "companion_survival") -> str:
    return json.dumps(summarize_state(state, mode=mode), ensure_ascii=False, indent=2)


def _fmt_item(item) -> str:
    """Format an equipment item (name string or {name, durability_pct} dict)."""
    if item is None:
        return '無'
    if isinstance(item, str):
        return item
    name = item.get('name') or '無'
    pct = item.get('durability_pct')
    if pct is not None:
        return f"{name} ({pct}%)"
    return name


def equipment_summary(state: dict) -> str:
    """Format current equipment as a human-readable string for LLM prompts."""
    equipment = state.get('equipment') or {}
    armor = equipment.get('armor') or {}
    lines = [
        f"主手：{_fmt_item(equipment.get('main_hand'))}",
        f"頭盔：{_fmt_item(armor.get('head'))}",
        f"胸甲：{_fmt_item(armor.get('torso'))}",
        f"護腿：{_fmt_item(armor.get('legs'))}",
        f"靴子：{_fmt_item(armor.get('feet'))}",
    ]
    return "\n".join(lines)
