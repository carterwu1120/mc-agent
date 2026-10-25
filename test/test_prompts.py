import re

import pytest

from agent.handlers import (coordinator, craft_decision, inventory, planner, recovery, respawn,
                            self_task, task_arbitration, tool_durability)
from agent.handlers.prompts import PROMPTS_DIR, placeholders, render_prompt

TEMPLATES = sorted(p.stem for p in PROMPTS_DIR.glob('*.md') if p.stem != 'README')
HANDLERS_DIR = PROMPTS_DIR.parent / 'handlers'


def _rendered():
    return [
        planner.SYSTEM_PROMPT, self_task.SYSTEM_PROMPT, respawn.SYSTEM_PROMPT,
        tool_durability.SYSTEM_PROMPT, craft_decision.SYSTEM_PROMPT,
        craft_decision.SYSTEM_PROMPT_MATERIAL, inventory.SYSTEM_PROMPT,
        coordinator.SYSTEM_PROMPT, task_arbitration.SYSTEM_PROMPT,
        recovery.system_prompt({'activity': 'mining', 'reason': 'no_tools'}),
    ]


@pytest.mark.parametrize('name', TEMPLATES)
def test_every_template_is_rendered_by_a_handler(name):
    sources = ''.join(p.read_text(encoding='utf-8') for p in HANDLERS_DIR.glob('*.py'))
    assert re.search(rf'render_prompt\(\s*"{name}"', sources)


def test_rendered_prompts_have_no_leftover_placeholders():
    for prompt in _rendered():
        assert not re.search(r'\$[A-Za-z_]', prompt)


def test_missing_or_unexpected_values_are_errors():
    with pytest.raises(KeyError):
        render_prompt('inventory')
    with pytest.raises(KeyError):
        render_prompt('coordinator', extra='x')


def test_placeholders_are_declared_by_name():
    assert placeholders('recovery') == {'commands', 'knowledge'}
    assert placeholders('coordinator') == set()
