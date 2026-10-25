import asyncio
from unittest.mock import AsyncMock

from agent.handlers import tool_durability


def test_mining_pickaxe_durability_is_handled_locally_without_llm():
    llm = type('LLM', (), {'chat': AsyncMock()})()
    state = {
        'activity': 'mining',
        'items': [{'item': 'wooden_pickaxe', 'durability_pct': 10}],
        'inventory': [{'name': 'cobblestone', 'count': 12}],
    }

    result = asyncio.run(tool_durability.handle(state, llm))

    assert result is None
    llm.chat.assert_not_called()
