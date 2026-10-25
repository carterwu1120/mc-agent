"""Bounded, in-place inventory cleanup for the current task only."""
import json
import re
from agent import task_memory
from agent.brain import LLMClient
from agent.handlers.knowledge import knowledge
from agent.handlers.prompts import render_prompt
from agent.handlers.llm_response import parse_llm_json

SYSTEM_PROMPT = render_prompt('inventory', knowledge=knowledge('inventory'))
# Only ordinary byproducts can be proposed for disposal. Tools, food, fuel,
# ores, wood and crafting dependencies never pass this allowlist.
DISPOSABLE = {'cobblestone', 'cobbled_deepslate', 'diorite', 'andesite', 'granite',
              'tuff', 'gravel', 'dirt', 'coarse_dirt', 'netherrack', 'flint',
              'rotten_flesh', 'spider_eye', 'bone', 'clay_ball', 'sand', 'calcite'}
OUTPUTS = {'stone': 'cobblestone', 'deepslate': 'cobbled_deepslate',
           'iron': 'raw_iron', 'gold': 'raw_gold', 'copper': 'raw_copper'}


def protected_items(task):
    protected = set()
    objective = (task.get('context') or {}).get('objective') or {}
    if objective.get('item'):
        protected.add(objective['item'])
    commands = [task.get('goal', '')] + list(task.get('commands') or [])
    for command in commands:
        parts = command.split()
        if len(parts) > 1 and parts[0] in {'mine', 'smelt'}:
            protected.add(OUTPUTS.get(parts[1], parts[1]))
    return protected


def safe_drops(items, inventory, task):
    counts = {}
    for item in inventory:
        counts[item['name']] = counts.get(item['name'], 0) + item.get('count', 0)
    protected = protected_items(task)
    drops = []
    seen = set()
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        name, count = item.get('name'), item.get('count')
        if not isinstance(name, str) or name in seen or name not in DISPOSABLE or name in protected:
            continue
        seen.add(name)
        if type(count) is not int or count <= 0:
            continue
        # Preserve a working stack for local bridging/sealing and furnace crafting.
        reserve = 64 if name in {'cobblestone', 'cobbled_deepslate', 'dirt', 'netherrack'} else 0
        count = min(count, max(0, counts.get(name, 0) - reserve))
        if count:
            drops.append({'name': name, 'count': count})
    return drops


async def handle(state: dict, llm: LLMClient) -> dict:
    inventory = state.get('inventory') or []
    task = task_memory.load() or {}
    prompt = json.dumps({'inventory': inventory, 'current_task': {
        'goal': task.get('goal'), 'commands': task.get('commands', []),
        'currentStep': task.get('currentStep')},
        'protected_items': sorted(protected_items(task)),
        'disposable_candidates': sorted(DISPOSABLE),
        'activity': state.get('activity'), 'food': state.get('food'),
        'health': state.get('health')}, ensure_ascii=False)
    try:
        response = await llm.chat([{'role': 'user', 'content': prompt}], system=SYSTEM_PROMPT)
        clean = re.sub(r'^```[a-z]*\s*|```$', '', response.strip()).strip()
        decision = parse_llm_json(json.loads(clean), 'inventory')
        drops = safe_drops(decision.get('items'), inventory, task) if decision.get('action') == 'drop' else []
        if drops:
            return {'command': 'inventory_decision', 'action': 'drop', 'items': drops}
    except Exception as error:
        print(f'[Inventory] 無可用整理決策: {type(error).__name__}')
    return {'command': 'inventory_decision', 'action': 'blocked'}
