import asyncio
import json
from unittest.mock import AsyncMock

from agent.skills import recovery


def test_supervised_replan_cannot_drop_current_goal_or_pending_steps():
    llm = type('LLM', (), {
        'chat': AsyncMock(return_value=json.dumps({
            'action': 'replan',
            'commands': ['stopmine'],
            'expected_outcome': '停止舊路線後再繼續原目標',
            'text': '先停止卡住的挖礦。',
        }))
    })()
    state = {
        'activity': 'mining',
        'reason': 'no_progress',
        'inventory': [],
        'equipment': {},
        'plan_context': {
            'goal': 'mine diamond 10',
            'current_cmd': 'mine diamond 10',
            'pending_steps': ['equip'],
            'done_steps': [],
        },
    }

    result = asyncio.run(recovery.handle(state, llm))

    assert result['commands'] == [
        'stopmine', 'mine diamond 10', 'equip',
    ]
