"""Inventory-based mining objective, independent of the current strategy."""
ITEMS = {'stone': 'cobblestone', 'iron': 'raw_iron', 'gold': 'raw_gold',
         'copper': 'raw_copper', 'lapis': 'lapis_lazuli', 'redstone': 'redstone'}


def count(state, item):
    return sum(i.get('count', 0) for i in (state or {}).get('inventory', [])
               if i.get('name') == item)


def create(goal, state):
    parts = str(goal or '').split()
    if len(parts) != 3 or parts[0] != 'mine':
        return None
    try:
        quantity = int(parts[2])
    except ValueError:
        return None
    if quantity <= 0 or 'inventory' not in state:
        return None
    item = ITEMS.get(parts[1], parts[1])
    return {'kind': 'mine', 'target': parts[1], 'item': item,
            'count': quantity, 'baseline': count(state, item)}


def progress(objective, state):
    if not objective or 'inventory' not in (state or {}):
        return None
    gained = max(0, count(state, objective['item']) - objective['baseline'])
    return {**objective, 'gained': gained,
            'remaining': max(0, objective['count'] - gained)}
