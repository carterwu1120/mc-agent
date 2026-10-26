import json

import pytest

from agent import task_memory


@pytest.fixture
def memory(tmp_path, monkeypatch):
    monkeypatch.setattr(task_memory, 'FILE', str(tmp_path / 'task.json'))
    for name in ('archive_task', 'write_event', 'write_failure'):
        monkeypatch.setattr(task_memory.history_db, name, lambda *a, **k: None)
    return tmp_path / 'task.json'


def test_task_file_holds_only_the_current_task_in_reading_order(memory):
    task_memory.save('挖鑽石', ['preparetool iron_pickaxe', 'mine diamond 10'], source='player')
    task_memory.mark_step_running(0)
    task_memory.mark_step_failed(0, 'no_iron')
    task_memory.update_context({'workPos': {'x': 1, 'y': -58, 'z': 2}})

    data = json.loads(memory.read_text())
    assert list(data) == ['id', 'goal', 'status', 'source', 'createdAt', 'currentStep', 'steps', 'context', 'runtime']
    assert data['steps'] == [
        {'cmd': 'preparetool iron_pickaxe', 'status': 'failed', 'error': 'no_iron'},
        {'cmd': 'mine diamond 10', 'status': 'pending'},
    ]

    task_memory.done(goal_count=10, actual_count=11)
    data = json.loads(memory.read_text())
    assert (data['status'], data['goalVerified'], data['goal_count'], data['actual_count']) == ('done', True, 10, 11)


def test_old_files_lose_unused_keys_and_step_snapshots(memory):
    memory.write_text(json.dumps({
        'id': 'old', 'goal': 'g', 'status': 'running', 'reason': '', 'final_goal': None,
        'commands': ['mine iron 3'], 'recentTransitions': [{'fromGoal': 'a'}], 'recentEvents': [],
        'steps': [{'cmd': 'mine iron 3', 'reason': '取得 iron', 'status': 'running', 'error': None,
                   'context': {'currentPos': {}, 'activityStack': ['mining']}}],
        'currentStep': 0, 'runtime': {'activityStack': ['mining'], 'supervision': {'phase': 'idle'}},
    }))
    task_memory.update_step(0)
    data = json.loads(memory.read_text())
    assert set(data) == {'id', 'goal', 'status', 'currentStep', 'steps', 'context', 'runtime'}
    assert data['steps'] == [{'cmd': 'mine iron 3', 'status': 'running'}]
    assert data['runtime'] == {'supervision': {'phase': 'idle'}}


def test_archive_keeps_the_command_list_for_the_database(memory, monkeypatch):
    archived = []
    monkeypatch.setattr(task_memory.history_db, 'archive_task', archived.append)
    task_memory.save('g', ['chop logs 3'])
    task_memory.interrupt('player_stop')
    assert archived[0]['commands'] == ['chop logs 3']
    assert task_memory.load()['interruptedBy'] == 'player_stop'
