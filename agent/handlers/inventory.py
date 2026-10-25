"""In-place inventory cleanup for the current task.

Layers, cheapest first:
1. Hard guard (code): tools, armor, food, fuel, valuables, crafting essentials, the
   task goal and unfinished steps' items are never dropped.
2. Building reserve: keep at least BUILDING_RESERVE blocks for pillaring/bridging.
3. Junk: always dropped.
4. Agent judgment: only if that does not free enough slots, the LLM decides which of
   the remaining "misc" items the current task does not need.
A failed or unusable LLM reply falls back to layers 2-3 instead of stopping the task.
"""
import json

from agent import task_memory
from agent.brain import LLMClient
from agent.handlers.knowledge import knowledge
from agent.handlers.llm_response import parse_json_object
from agent.handlers.prompts import render_prompt

SYSTEM_PROMPT = render_prompt('inventory', knowledge=knowledge('inventory'))

# Keep this guard in sync with isGuarded() in agent/bot/inventory.js.
TOOL_SUFFIXES = ('_pickaxe', '_axe', '_shovel', '_hoe', '_sword', '_helmet', '_chestplate',
                 '_leggings', '_boots', '_horse_armor')
TOOLS = {'bow', 'crossbow', 'trident', 'shield', 'fishing_rod', 'shears', 'flint_and_steel',
         'compass', 'clock', 'elytra', 'bucket', 'water_bucket', 'lava_bucket', 'arrow'}
FOOD = {'bread', 'apple', 'golden_apple', 'enchanted_golden_apple', 'golden_carrot', 'carrot',
        'potato', 'baked_potato', 'beetroot', 'melon_slice', 'sweet_berries', 'cookie',
        'pumpkin_pie', 'mushroom_stew', 'beetroot_soup', 'rabbit_stew', 'dried_kelp',
        'beef', 'porkchop', 'chicken', 'mutton', 'rabbit', 'cod', 'salmon'}
FUEL = {'coal', 'charcoal', 'coal_block', 'blaze_rod', 'stick'}
VALUABLES = {'diamond', 'emerald', 'lapis_lazuli', 'redstone', 'quartz', 'amethyst_shard',
             'netherite_scrap', 'ancient_debris', 'diamond_block', 'emerald_block', 'lapis_block',
             'redstone_block', 'iron_block', 'gold_block', 'copper_block', 'netherite_block'}
ESSENTIALS = {'crafting_table', 'furnace', 'chest', 'torch', 'string'}

# Placeable blocks kept for pillaring/bridging, in the order they are kept.
BUILDING = ('cobblestone', 'cobbled_deepslate', 'dirt', 'netherrack', 'stone')
BUILDING_RESERVE = 128
JUNK = {'diorite', 'andesite', 'granite', 'tuff', 'calcite', 'gravel', 'flint', 'sand',
        'coarse_dirt', 'rooted_dirt', 'pointed_dripstone', 'dripstone_block', 'rotten_flesh',
        'spider_eye', 'bone', 'poisonous_potato', 'clay_ball'}

INVENTORY_FULL = 34
TARGET_USED = 28  # free well below the full threshold so mining does not refill at once
STEP_ITEMS = {'stone': ['cobblestone'], 'deepslate': ['cobbled_deepslate'],
              'iron': ['raw_iron', 'iron_ingot'], 'raw_iron': ['raw_iron', 'iron_ingot'],
              'gold': ['raw_gold', 'gold_ingot'], 'raw_gold': ['raw_gold', 'gold_ingot'],
              'copper': ['raw_copper', 'copper_ingot'], 'raw_copper': ['raw_copper', 'copper_ingot']}


def is_guarded(name: str) -> bool:
    """Item categories that are never dropped, whatever the task."""
    return (name.endswith(TOOL_SUFFIXES) or name in TOOLS or name in FOOD
            or name.startswith('cooked_') or name in FUEL or name in ESSENTIALS
            or name in VALUABLES or name.endswith(('_ore', '_ingot', '_nugget', '_log', '_wood', '_planks'))
            or name.startswith('raw_'))


def protected_items(task: dict) -> set[str]:
    """Items the task still needs: its goal and the items of unfinished mine/smelt steps."""
    protected = set()
    objective = (task.get('context') or {}).get('objective') or {}
    if objective.get('item'):
        protected.add(objective['item'])
    # Finished steps no longer need their output; a done `mine stone 11` used to
    # protect every cobblestone and leave 700 of them undroppable.
    pending = [s.get('cmd', '') for s in task.get('steps') or [] if s.get('status') != 'done']
    for command in [task.get('goal') or ''] + pending:
        parts = command.split()
        if len(parts) > 1 and parts[0] in {'mine', 'smelt'}:
            protected.update(STEP_ITEMS.get(parts[1], [parts[1]]))
    return protected


def _totals(inventory: list) -> tuple[dict, dict]:
    counts, slots = {}, {}
    for item in inventory:
        counts[item['name']] = counts.get(item['name'], 0) + item.get('count', 0)
        slots[item['name']] = slots.get(item['name'], 0) + 1
    return counts, slots


def rule_drops(inventory: list, task: dict) -> list[dict]:
    """Junk and building blocks beyond the shared reserve; no judgment needed."""
    counts, _ = _totals(inventory)
    protected = protected_items(task)
    drops = []
    keep = BUILDING_RESERVE
    for name in BUILDING:
        if name not in counts or name in protected:
            continue
        kept = min(counts[name], keep)
        keep -= kept
        if counts[name] > kept:
            drops.append({'name': name, 'count': counts[name] - kept})
    for name in sorted(counts):
        if name in JUNK and name not in protected:
            drops.append({'name': name, 'count': counts[name]})
    return drops


def misc_candidates(inventory: list, task: dict) -> list[str]:
    """Items the agent may judge: not guarded, not needed by the task, not building or junk."""
    counts, _ = _totals(inventory)
    protected = protected_items(task)
    return sorted(n for n in counts if not is_guarded(n) and n not in protected
                  and n not in BUILDING and n not in JUNK)


def _slots_after(inventory: list, drops: list[dict]) -> int:
    counts, slots = _totals(inventory)
    used = len(inventory)
    for drop in drops:
        remaining = counts.get(drop['name'], 0) - drop['count']
        used -= slots.get(drop['name'], 0) - (-(-remaining // 64) if remaining > 0 else 0)
    return used


def _validated(raw, candidates: list[str], counts: dict) -> list[dict]:
    drops = []
    for item in (raw or {}).get('drop') or [] if isinstance(raw, dict) else []:
        if not isinstance(item, dict):
            continue
        name, count = item.get('name'), item.get('count')
        if name in candidates and type(count) is int and count > 0 and name not in {d['name'] for d in drops}:
            drops.append({'name': name, 'count': min(count, counts[name])})
    return drops


async def handle(state: dict, llm: LLMClient) -> dict:
    inventory = state.get('inventory') or []
    task = task_memory.load() or {}
    drops = rule_drops(inventory, task)
    candidates = misc_candidates(inventory, task)
    if _slots_after(inventory, drops) > TARGET_USED and candidates:
        counts, _ = _totals(inventory)
        prompt = json.dumps({
            'current_task': {'goal': task.get('goal'),
                             'remaining_steps': [s.get('cmd') for s in task.get('steps') or [] if s.get('status') != 'done']},
            'inventory': counts,
            'already_dropping': drops,
            'candidates': {name: counts[name] for name in candidates},
            'slots_used_after_current_drops': _slots_after(inventory, drops),
            'target_slots_used': TARGET_USED,
        }, ensure_ascii=False)
        try:
            response = await llm.chat([{'role': 'user', 'content': prompt}], system=SYSTEM_PROMPT)
            drops += _validated(parse_json_object(response), candidates, counts)
        except Exception as error:
            # The rule-based drops still apply; a bad reply must not stop the task.
            print(f'[Inventory] 判斷雜項失敗，只套用規則丟棄: {type(error).__name__}')
    if drops:
        print(f'[Inventory] 原地丟棄: {drops}')
        return {'command': 'inventory_decision', 'action': 'drop', 'items': drops}
    print('[Inventory] 沒有可安全丟棄的物品')
    return {'command': 'inventory_decision', 'action': 'blocked'}
