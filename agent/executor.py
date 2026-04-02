import asyncio
import json
from agent import task_memory


class PlanExecutor:
    def __init__(self):
        self._running = False
        self._done = asyncio.Event()
        self._stuck_event = asyncio.Event()
        self._replan_commands: list | None = None
        self._in_stuck_recovery = False
        self._current_command = None
        self._current_step_index = 0
        self._ws = None
        self._step_results: list[dict] = []

    async def execute(self, commands: list, ws, goal: str = "") -> None:
        self._running = True
        self._ws = ws
        self._replan_commands = None
        self._step_results = []

        if goal:
            task_memory.save(goal, commands)

        print(f'[Executor] 開始執行計畫：{commands}')

        i = 0
        while i < len(commands):
            if not self._running:
                print('[Executor] 計畫已中止')
                task_memory.mark_step_failed(i, "aborted")
                break

            cmd_str = commands[i]
            task_memory.update_step(i)
            task_memory.mark_step_running(i)
            self._current_step_index = i

            msg = _parse(cmd_str)
            self._current_command = msg
            print(f'[Executor] 執行步驟 {i}: {cmd_str}')
            await ws.send(json.dumps(msg))

            self._done.clear()
            self._stuck_event.clear()

            timed_out = False
            try:
                done_task = asyncio.ensure_future(self._done.wait())
                stuck_task = asyncio.ensure_future(self._stuck_event.wait())
                finished, _ = await asyncio.wait(
                    [done_task, stuck_task],
                    timeout=120.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                done_task.cancel()
                stuck_task.cancel()

                if not finished:
                    # Real timeout — neither event fired
                    print(f'[Executor] 等待 "{cmd_str}" 超時，中止計畫')
                    task_memory.mark_step_failed(i, "timeout")
                    self._step_results.append({"cmd": cmd_str, "status": "failed", "error": "timeout"})
                    timed_out = True
                    break

                if self._stuck_event.is_set() and not self._done.is_set():
                    # Paused for stuck handling — wait for resume or replan
                    print(f'[Executor] 步驟 {i} 因 activity_stuck 暫停，等待 LLM 決策...')
                    await self._done.wait()

                    if self._replan_commands is not None:
                        new_cmds = self._replan_commands
                        self._replan_commands = None
                        print(f'[Executor] 接受重新規劃：{new_cmds}')
                        task_memory.mark_step_failed(i, "replanned")
                        self._step_results.append({"cmd": cmd_str, "status": "replanned", "error": "replanned"})
                        task_memory.replace_remaining_steps(i, new_cmds)
                        commands = commands[:i] + new_cmds
                        self._current_command = None
                        continue  # don't increment i — commands[i] is now the first new step

                    # Single-step recovery completed — continue plan
                    task_memory.mark_step_done(i)
                    self._step_results.append({"cmd": cmd_str, "status": "done"})
                else:
                    # Normal completion
                    task_memory.mark_step_done(i)
                    self._step_results.append({"cmd": cmd_str, "status": "done"})

            except asyncio.CancelledError:
                task_memory.mark_step_failed(i, "cancelled")
                self._step_results.append({"cmd": cmd_str, "status": "failed", "error": "cancelled"})
                break
            finally:
                self._current_command = None

            i += 1

        if self._running:
            task_memory.done()
            await self._send_summary(ws)
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
            'makechest', 'chat', 'setmode', 'resumetask',
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

    def notify_stuck(self) -> None:
        """Signal that activity_stuck fired. Pauses executor until resume or replan."""
        if self._running:
            self._in_stuck_recovery = True
            self._stuck_event.set()

    def replan(self, new_commands: list) -> None:
        """Accept new plan from LLM. Unblocks the paused executor."""
        self._in_stuck_recovery = False
        self._replan_commands = new_commands
        self._done.set()

    def resume_after_stuck(self) -> None:
        """Called after single-step stuck recovery finishes. Resumes the plan."""
        self._in_stuck_recovery = False
        self._done.set()

    def is_in_stuck_recovery(self) -> bool:
        return self._in_stuck_recovery

    def abort(self) -> None:
        if self._running:
            task_memory.mark_step_failed(self._current_step_index, "aborted")
        self._running = False
        self._current_command = None
        self._replan_commands = None
        self._in_stuck_recovery = False
        self._done.set()
        self._stuck_event.set()

    def is_running(self) -> bool:
        return self._running

    async def _send_summary(self, ws) -> None:
        if not self._step_results:
            return
        parts = []
        for r in self._step_results:
            label = r["cmd"].split()[0]
            status = r["status"]
            if status == "done":
                parts.append(f"{label}✓")
            elif status in ("failed", "timeout", "cancelled"):
                err = r.get("error", "")
                parts.append(f"{label}✗({err})" if err else f"{label}✗")
            elif status == "replanned":
                parts.append(f"{label}→重規")
        if parts:
            text = "完成！" + " ".join(parts)
            try:
                await ws.send(json.dumps({"command": "chat", "text": text}))
            except Exception:
                pass
            print(f'[Executor] 摘要: {text}')


def _parse(cmd_str: str) -> dict:
    """Parse "mine diamond 41" → {"command": "mine", "args": ["diamond", "41"]}"""
    parts = cmd_str.split()
    return {'command': parts[0], 'args': parts[1:] if len(parts) > 1 else []}
