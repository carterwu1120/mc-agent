import asyncio

import pytest

from agent.brain.cli_agent import CLIProviderError
from agent.handlers import task_arbitration

STATE = {'message': 'mine diamond 10', 'inventory': [], 'activity': 'idle'}


class FailingLLM:
    async def chat(self, *args, **kwargs):
        raise CLIProviderError('claude', 'process_error', 'process exited with code 1')


class GarbageLLM:
    async def chat(self, *args, **kwargs):
        return 'not json'


@pytest.fixture(autouse=True)
def no_history(monkeypatch):
    monkeypatch.setattr(task_arbitration.task_memory, 'recent_events', lambda *a, **k: [])
    monkeypatch.setattr(task_arbitration.task_memory, 'recent_failures', lambda *a, **k: [])


def test_provider_failure_is_not_reported_as_a_misunderstanding():
    with pytest.raises(CLIProviderError):
        asyncio.run(task_arbitration.handle(dict(STATE), FailingLLM()))


def test_unparseable_answer_still_returns_none():
    assert asyncio.run(task_arbitration.handle(dict(STATE), GarbageLLM())) is None
