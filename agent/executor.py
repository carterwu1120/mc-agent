import asyncio
import json


class PlanExecutor:
    def __init__(self):
        self._running = False
        self._done = asyncio.Event()
        self._current_command = None

    async def execute(self, commands: list, ws) -> None:
        self._running = True
        print(f'[Executor] 開始執行計畫：{commands}')
        for cmd_str in commands:
            if not self._running:
                print('[Executor] 計畫已中止')
                break
            msg = _parse(cmd_str)
            self._current_command = msg
            print(f'[Executor] 執行: {cmd_str}')
            await ws.send(json.dumps(msg))
            self._done.clear()
            try:
                await asyncio.wait_for(self._done.wait(), timeout=120.0)
            except asyncio.TimeoutError:
                print(f'[Executor] 等待 "{cmd_str}" 超時，中止計畫')
                break
            finally:
                self._current_command = None
        self._running = False
        print('[Executor] 計畫執行完畢')

    def signal_done(self, state: dict | None = None) -> None:
        if not self._current_command:
            self._done.set()
            return

        event_type = (state or {}).get('type')
        command = self._current_command.get('command')

        immediate_commands = {
            'stopmine', 'stopchop', 'stopfish', 'stopsmelt', 'stopcombat', 'stophunt', 'stopgetfood', 'stopsurface', 'stopexplore',
            'home', 'back', 'sethome', 'equip', 'unequip', 'deposit', 'withdraw', 'readchest', 'setchest', 'labelchest',
            'makechest', 'chat',
        }
        if event_type == 'action_done':
            if command in immediate_commands:
                self._done.set()
            return

        if event_type != 'activity_done':
            return

        expected_activity = {
            'mine': 'mining',
            'chop': 'chopping',
            'fish': 'fishing',
            'smelt': 'smelting',
            'combat': 'combat',
            'hunt': 'hunting',
            'getfood': 'getfood',
            'surface': 'surface',
            'explore': 'explore',
        }.get(command)

        if expected_activity and (state or {}).get('activity') == expected_activity:
            self._done.set()

    def abort(self) -> None:
        self._running = False
        self._current_command = None
        self._done.set()

    def is_running(self) -> bool:
        return self._running


def _parse(cmd_str: str) -> dict:
    """Parse "mine diamond 41" → {"command": "mine", "args": ["diamond", "41"]}"""
    parts = cmd_str.split()
    return {'command': parts[0], 'args': parts[1:] if len(parts) > 1 else []}
