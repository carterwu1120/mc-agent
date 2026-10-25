import asyncio
import json

from agent.handlers import inventory


def stacks(name, total):
    return [{'name': name, 'count': min(64, total - i)} for i in range(0, total, 64)]


TASK = {'goal': 'mine diamond 10', 'steps': [
    {'cmd': 'mine stone 11', 'status': 'done'},
    {'cmd': 'mine iron 2', 'status': 'interrupted'},
    {'cmd': 'smelt raw_iron 2', 'status': 'pending'},
    {'cmd': 'mine diamond 8', 'status': 'pending'},
]}


class LLM:
    def __init__(self, reply):
        self.reply, self.calls = reply, []

    async def chat(self, messages, system=None, reasoning_effort=None):
        self.calls.append(json.loads(messages[0]['content']))
        return self.reply


def run(state, llm, monkeypatch, task=TASK):
    monkeypatch.setattr(inventory.task_memory, 'load', lambda: task)
    return asyncio.run(inventory.handle(state, llm))


def test_finished_steps_do_not_protect_their_output():
    # Seen live: a done `mine stone 11` protected 724 cobblestone (12 slots).
    assert 'cobblestone' not in inventory.protected_items(TASK)
    assert {'raw_iron', 'iron_ingot', 'diamond'} <= inventory.protected_items(TASK)


def test_live_inventory_is_cleaned_by_rules_without_a_model_call(monkeypatch):
    # The inventory from the stuck run: rules free enough, so no LLM call is made.
    state = {'inventory': stacks('cobblestone', 724) + [
        {'name': 'andesite', 'count': 7}, {'name': 'lapis_lazuli', 'count': 7}, {'name': 'lapis_block', 'count': 2},
        {'name': 'iron_ingot', 'count': 1}, {'name': 'diamond', 'count': 2}, {'name': 'feather', 'count': 9},
        {'name': 'phantom_membrane', 'count': 4}, {'name': 'stone_pickaxe', 'count': 1}, {'name': 'cooked_beef', 'count': 22}]
        + [{'name': f'filler_{i}', 'count': 1} for i in range(14)]}
    llm = LLM('{"drop":[]}')
    decision = run(state, llm, monkeypatch)
    assert {d['name']: d['count'] for d in decision['items']} == {'cobblestone': 596, 'andesite': 7}
    assert llm.calls == [], 'junk and surplus building blocks already free enough slots'


def test_building_reserve_is_shared_and_prefers_cobblestone():
    drops = inventory.rule_drops([{'name': 'cobblestone', 'count': 100}, {'name': 'dirt', 'count': 60}], {})
    assert drops == [{'name': 'dirt', 'count': 32}]


def test_agent_judges_misc_items_when_rules_are_not_enough(monkeypatch):
    state = {'inventory': [{'name': f'misc_{i}', 'count': 1} for i in range(30)]
             + [{'name': 'diamond_pickaxe', 'count': 1}, {'name': 'feather', 'count': 9},
                {'name': 'white_wool', 'count': 3}, {'name': 'diamond', 'count': 2}]}
    llm = LLM('Sure: {"drop":[{"name":"feather","count":9,"reason":"unused"},'
              '{"name":"diamond","count":2},{"name":"diamond_pickaxe","count":1}]}')
    decision = run(state, llm, monkeypatch)
    assert decision['items'] == [{'name': 'feather', 'count': 9}], 'guarded items are never dropped'
    candidates = llm.calls[0]['candidates']
    assert 'feather' in candidates and 'diamond' not in candidates and 'diamond_pickaxe' not in candidates


def test_unusable_model_reply_falls_back_to_rule_drops(monkeypatch):
    state = {'inventory': [{'name': f'misc_{i}', 'count': 1} for i in range(32)] + [{'name': 'gravel', 'count': 20}]}
    decision = run(state, LLM('I cannot decide.'), monkeypatch)
    assert decision['items'] == [{'name': 'gravel', 'count': 20}]


def test_nothing_droppable_is_blocked(monkeypatch):
    task = {'goal': 'mine stone 200', 'steps': [{'cmd': 'mine stone 200', 'status': 'running'}]}
    decision = run({'inventory': stacks('cobblestone', 300)}, LLM('{"drop":[]}'), monkeypatch, task=task)
    assert decision == {'command': 'inventory_decision', 'action': 'blocked'}
