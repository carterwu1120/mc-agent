from agent.mining_objective import create, progress
from agent.skills.recovery import _restore_remaining_goal
from agent.skills.command_validation import validate_command


def state(**items):
    return {'inventory': [{'name': k, 'count': v} for k, v in items.items()]}


def test_objective_tracks_new_diamonds_not_coal_or_existing_inventory():
    objective = create('mine diamond 10', state(diamond=3))
    assert progress(objective, state(diamond=5, coal=64))['remaining'] == 8
    assert progress(objective, state(diamond=13))['remaining'] == 0
    assert progress(objective, {}) is None


def test_prerequisite_plan_does_not_repeat_full_goal():
    context = {'objective': create('mine diamond 10', state()),
               'current_cmd': 'mine diamond 10', 'pending_steps': []}
    assert _restore_remaining_goal(['preparetool iron_pickaxe'], context) == ['preparetool iron_pickaxe']
    assert validate_command('preparetool iron_pickaxe') is None
    assert validate_command('preparetool unknown') is not None


def test_objective_survives_replan_and_resume(tmp_path, monkeypatch):
    from agent import task_memory
    monkeypatch.setattr(task_memory, 'FILE', str(tmp_path / 'task.json'))
    task = task_memory.save('mine diamond 10', ['mine diamond 10'])
    objective = create(task['goal'], state(diamond=2))
    task_memory.update_context({'objective': objective})
    task_memory.replace_remaining_steps(0, ['mine iron 3', 'preparetool iron_pickaxe'])
    task_memory.interrupt('no_tools')
    restored = task_memory.resume_interrupted(['preparetool iron_pickaxe', 'mine diamond 8'])
    assert restored['id'] == task['id']
    assert restored['context']['objective'] == objective
    assert progress(restored['context']['objective'], state(diamond=4))['remaining'] == 8


def test_finishing_prerequisite_does_not_complete_diamond_task(tmp_path, monkeypatch):
    import asyncio
    import json
    from agent import task_memory
    from agent.executor import PlanExecutor
    monkeypatch.setattr(task_memory, 'FILE', str(tmp_path / 'task.json'))

    async def run():
        executor = PlanExecutor()
        executor.update_state(state(diamond=2))
        recoveries = []

        async def recover(event, ws):
            recoveries.append(event)

        async def send(raw):
            command = json.loads(raw)
            if command['command'] == 'preparetool':
                executor.signal_done({'type': 'action_done', 'command_id': command['_command_id']})

        executor._verify_failed_callback = recover
        socket = type('Socket', (), {'send': staticmethod(send)})()
        await asyncio.wait_for(executor.execute(['preparetool iron_pickaxe'], socket, goal='mine diamond 10'), 2)
        task = task_memory.load_any()
        assert task['status'] == 'interrupted'
        assert task['context']['objective']['baseline'] == 2
        assert [event['reason'] for event in recoveries] == ['objective_incomplete']

    asyncio.run(run())
