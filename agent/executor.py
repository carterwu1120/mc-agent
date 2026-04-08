import asyncio
import json
from agent import task_memory
from agent.plan_utils import normalize_commands


HEARTBEAT_TIMEOUT = 30.0   # seconds without a tick before declaring JS unresponsive
POLL_INTERVAL    = 10.0   # how often to check heartbeat while waiting

class PlanExecutor:
    def __init__(self):
        self._running = False
        self._done = asyncio.Event()
        self._stuck_event = asyncio.Event()
        self._skip_event = asyncio.Event()
        self._replan_commands: list | None = None
        self._in_stuck_recovery = False
        self._current_command = None
        self._current_step_index = 0
        self._ws = None
        self._step_results: list[dict] = []
        self._last_heartbeat: float = 0.0
        self._run_id: int = 0
        self._context: dict = {}  # runtime values substituted into later commands

    def heartbeat(self) -> None:
        """Called on every tick event to confirm JS is still alive."""
        self._last_heartbeat = asyncio.get_event_loop().time()

    async def execute(self, commands: list, ws, goal: str = "") -> None:
        self._run_id += 1
        my_run_id = self._run_id
        self._running = True
        self._ws = ws
        self._replan_commands = None
        self._step_results = []
        self._context = {}

        if goal:
            task_memory.save(goal, commands)

        print(f'[Executor] 開始執行計畫：{commands}')

        i = 0
        while i < len(commands):
            if not self._running:
                print('[Executor] 計畫已中止')
                task_memory.mark_step_failed(i, "aborted")
                break

            cmd_str = _substitute(commands[i], self._context)
            task_memory.update_step(i)
            task_memory.mark_step_running(i)
            self._current_step_index = i

            msg = _parse(cmd_str)
            self._current_command = msg
            print(f'[Executor] 執行步驟 {i}: {cmd_str}')
            await ws.send(json.dumps(msg))

            self._done.clear()
            self._stuck_event.clear()
            self._skip_event.clear()

            timed_out = False
            try:
                done_task = asyncio.ensure_future(self._done.wait())
                stuck_task = asyncio.ensure_future(self._stuck_event.wait())

                # Poll in short intervals, checking heartbeat between each.
                # Activity runs as long as JS keeps sending ticks — no fixed timeout.
                while True:
                    finished, _ = await asyncio.wait(
                        [done_task, stuck_task],
                        timeout=POLL_INTERVAL,
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if finished:
                        break  # done or stuck fired — proceed normally
                    # Neither fired yet — check JS is still alive
                    now = asyncio.get_event_loop().time()
                    if self._last_heartbeat > 0 and now - self._last_heartbeat > HEARTBEAT_TIMEOUT:
                        print(f'[Executor] JS 無回應超過 {HEARTBEAT_TIMEOUT}s，中止計畫')
                        done_task.cancel()
                        stuck_task.cancel()
                        task_memory.mark_step_failed(i, "timeout")
                        self._step_results.append({"cmd": cmd_str, "status": "failed", "error": "timeout"})
                        timed_out = True
                        break
                    elapsed = now - self._last_heartbeat
                    if elapsed > 10:
                        print(f'[Executor] 等待 "{cmd_str}" 中... (上次心跳 {elapsed:.0f}s 前)')

                if not timed_out:
                    done_task.cancel()
                    stuck_task.cancel()

                # abort() increments _run_id — detect superseded run and bail out
                if self._run_id != my_run_id:
                    return

                if self._stuck_event.is_set() and not self._done.is_set():
                    # Paused for stuck handling — wait for resume, replan, or skip
                    print(f'[Executor] 步驟 {i} 因 activity_stuck 暫停，等待 LLM 決策...')
                    await self._done.wait()

                    if self._skip_event.is_set():
                        # LLM decided this step is unrecoverable — skip it
                        print(f'[Executor] 步驟 {i} 被跳過: {cmd_str}')
                        task_memory.mark_step_failed(i, "skipped")
                        self._step_results.append({"cmd": cmd_str, "status": "failed", "error": "skipped"})
                        i += 1
                        self._current_command = None
                        continue

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
            task = task_memory.load()
            if task and task.get('status') == 'running':
                task_memory.done()
            await self._send_summary(ws)
        self._running = False
        print('[Executor] 計畫執行完畢')

    def signal_done(self, state: dict | None = None) -> None:
        if not self._current_command:
            self._done.set()
            return

        # Capture runtime values from action_done state for later substitution
        if state:
            if 'new_chest_id' in state:
                self._context['new_chest_id'] = str(state['new_chest_id'])

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

    def signal_done_after_stuck(self, state: dict | None = None) -> None:
        """Called during stuck recovery instead of signal_done.
        Only unblocks the executor if the completing activity matches the
        original step's expected activity. Recovery actions (e.g. surface
        completing during a chop step) keep the executor waiting."""
        event_type = (state or {}).get('type')

        if event_type == 'action_done':
            # Immediate recovery action finished — clear stuck flag and unblock
            self._in_stuck_recovery = False
            self._done.set()
            return

        if event_type == 'activity_done':
            command = (self._current_command or {}).get('command')
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
            completing = (state or {}).get('activity')
            if expected_activity and completing == expected_activity:
                # Original step finished after recovery — unblock
                self._in_stuck_recovery = False
                self._done.set()
            # else: recovery action finished (e.g. surface during chop step)
            # Keep waiting — original activity will send its own activity_done

    def notify_stuck(self) -> None:
        """Signal that activity_stuck fired. Pauses executor until resume or replan."""
        if self._running:
            self._in_stuck_recovery = True
            self._stuck_event.set()

    def replan(self, new_commands: list) -> None:
        """Accept new plan from LLM. Unblocks the paused executor."""
        self._in_stuck_recovery = False
        previous_command = None
        task = task_memory.load()
        if task:
            current_index = self._current_step_index
            if current_index > 0:
                steps = task.get("steps", [])
                if current_index - 1 < len(steps):
                    previous_command = steps[current_index - 1].get("cmd")
        self._replan_commands = normalize_commands(new_commands, previous_command=previous_command)
        self._done.set()

    def skip_step(self) -> None:
        """Skip the current stuck step and move to the next one."""
        if self._running and self._in_stuck_recovery:
            self._in_stuck_recovery = False
            self._skip_event.set()
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
        self._run_id += 1  # invalidate any running execute() coroutine
        self._running = False
        self._current_command = None
        self._replan_commands = None
        self._in_stuck_recovery = False
        self._done.set()
        self._stuck_event.set()
        self._skip_event.set()

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



def _substitute(cmd_str: str, context: dict) -> str:
    """Replace {key} placeholders in a command string with runtime context values."""
    for key, value in context.items():
        cmd_str = cmd_str.replace(f'{{{key}}}', value)
    return cmd_str


def _parse(cmd_str: str) -> dict:
    """Parse "mine diamond 41" → {"command": "mine", "args": ["diamond", "41"]}"""
    parts = cmd_str.split()
    return {'command': parts[0], 'args': parts[1:] if len(parts) > 1 else []}
