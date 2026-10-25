import re

import pytest

from agent.handlers import craft_decision, inventory, planner, recovery, respawn, self_task, tool_durability
from agent.handlers.commands_ref import COMMANDS
from agent.handlers.knowledge import KNOWLEDGE_DIR, knowledge

FILES = sorted(p.stem for p in KNOWLEDGE_DIR.glob('*.md') if p.stem != 'README')
# Bot commands handled by bot/commands.js but not listed in the Python registry.
EXTRA_BOT_COMMANDS = {'sethome', 'stopall'}
MINING_STUCK = {'activity': 'mining', 'reason': 'no_tools'}


def _all_prompts():
    return [
        planner.SYSTEM_PROMPT, self_task.SYSTEM_PROMPT, respawn.SYSTEM_PROMPT,
        tool_durability.SYSTEM_PROMPT, craft_decision.SYSTEM_PROMPT,
        craft_decision.SYSTEM_PROMPT_MATERIAL, inventory.SYSTEM_PROMPT,
        recovery.system_prompt(MINING_STUCK),
    ]


def test_every_knowledge_file_is_used_by_a_prompt():
    prompts = _all_prompts()
    for name in FILES:
        lines = [l for l in knowledge(name).splitlines() if l.startswith('- ')]
        assert any(lines[0] in p for p in prompts), name


def test_prompts_embed_their_knowledge():
    assert knowledge('inventory') in inventory.SYSTEM_PROMPT
    assert knowledge('planning') in planner.SYSTEM_PROMPT
    assert knowledge('respawn') in respawn.SYSTEM_PROMPT
    assert knowledge('durability') in tool_durability.SYSTEM_PROMPT
    assert knowledge('crafting', '選擇合成選項') in craft_decision.SYSTEM_PROMPT
    assert knowledge('crafting', '缺少合成材料') in craft_decision.SYSTEM_PROMPT_MATERIAL
    for prompt in (planner.SYSTEM_PROMPT, self_task.SYSTEM_PROMPT):
        assert knowledge('bot_capabilities', '鎬子與工具鏈') in prompt


def test_recovery_prompt_only_carries_the_stuck_activity():
    mining = recovery.system_prompt(MINING_STUCK)
    assert knowledge('recovery', '挖礦') in mining
    assert knowledge('bot_capabilities', '鎬子與工具鏈') in mining
    assert '## 冶煉' not in mining and '## 食物' not in mining
    assert '## watchdog' not in mining
    no_blocks = recovery.system_prompt({'activity': 'mining', 'reason': 'no_blocks'})
    assert '## 鎬子與工具鏈' not in no_blocks

    watchdog = recovery.system_prompt({'activity': 'chopping', 'activity_name': 'chopping', 'watchdog': True})
    assert knowledge('recovery', 'watchdog 判定（任何活動）') in watchdog
    assert knowledge('recovery', '砍樹') in watchdog

    idle_timeout = recovery.system_prompt({'activity': 'idle', 'plan_context': {'current_cmd': 'smelt raw_iron 3'}})
    assert knowledge('recovery', '冶煉') in idle_timeout

    tool = recovery.system_prompt({'activity': 'idle', 'reason': 'tool_preparation_failed'})
    assert knowledge('recovery', '工具準備') in tool

    unknown = recovery.system_prompt({'activity': 'idle', 'reason': 'unexpected_idle'})
    assert knowledge('recovery', '原則') in unknown and '## 挖礦' not in unknown


# The CLI resends the system prompt on every call. These bounds catch knowledge
# being pasted in wholesale; raise them deliberately, not to make a test pass.
@pytest.mark.parametrize('prompt, limit', [
    (lambda: recovery.system_prompt({'activity': 'mining', 'reason': 'no_blocks'}), 1900),
    (lambda: recovery.system_prompt(MINING_STUCK), 2400),
    (lambda: recovery.system_prompt({'activity': 'hunting', 'watchdog': True, 'activity_name': 'hunting'}), 2500),
    (lambda: self_task.SYSTEM_PROMPT, 2200),
    (lambda: respawn.SYSTEM_PROMPT, 2300),
    (lambda: tool_durability.SYSTEM_PROMPT, 1150),
    (lambda: craft_decision.SYSTEM_PROMPT_MATERIAL, 2000),
    (lambda: planner.SYSTEM_PROMPT, 4500),
    (lambda: inventory.SYSTEM_PROMPT, 1850),
])
def test_prompt_size_budget(prompt, limit):
    assert len(prompt()) <= limit


def test_missing_section_is_an_error():
    with pytest.raises(KeyError):
        knowledge('bot_capabilities', 'no such section')


@pytest.mark.parametrize('name', FILES)
def test_knowledge_is_interface_neutral(name):
    text = knowledge(name)
    assert 'mcctl' not in text
    assert '"action"' not in text


@pytest.mark.parametrize('name', FILES)
def test_backticked_commands_exist(name):
    for snippet in re.findall(r'`([^`]+)`', knowledge(name)):
        word = snippet.split()[0]
        if re.fullmatch(r'[a-z]+', word):
            assert word in COMMANDS or word in EXTRA_BOT_COMMANDS, f'{name}.md: `{snippet}`'


def test_preparetool_is_plannable_where_knowledge_recommends_it():
    assert 'preparetool' in planner._PLANNER_ALLOWED_KEYS
    assert 'preparetool' in self_task.ALLOWED_COMMANDS
    assert 'preparetool' in recovery.ALLOWED
