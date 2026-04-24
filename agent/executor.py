import asyncio
import json
from agent import task_memory
from agent.plan_utils import normalize_commands


HEARTBEAT_TIMEOUT = 30.0   # seconds without a tick before declaring JS unresponsive
POLL_INTERVAL    = 10.0   # how often to check heartbeat while waiting
ACTIVITY_START_TIMEOUT = 90.0  # seconds before retrying if expected activity never started


def _inventory_counts(inventory: list) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in (inventory or []):
        name = item.get("name")
        if name:
            counts[name] = counts.get(name, 0) + int(item.get("count", 0))
    return counts


def _cmd_to_activity(cmd_str: str) -> str:
    verb = cmd_str.split()[0] if cmd_str else ""
    return {
        'mine': 'mining', 'chop': 'chopping', 'fish': 'fishing',
        'smelt': 'smelting', 'hunt': 'hunting', 'getfood': 'getfood',
        'explore': 'explore', 'equip': 'equip', 'deposit': 'deposit',
    }.get(verb, verb)


def _verify_step(cmd_str: str, before: dict | None, after: dict | None) -> str | None:
    """
    Compare before/after state for a completed command.
    Returns a warning string if something looks wrong, else None.
    """
    if not before or not after:
        return None

    parts = cmd_str.split()
    verb = parts[0] if parts else ""

    if verb == "equip":
        before_eq = before.get("equipment") or {}
        after_eq  = after.get("equipment") or {}
        if before_eq == after_eq:
            return "equip 後裝備狀態未改變，可能裝備失敗"

    elif verb == "smelt" and len(parts) >= 3:
        target_raw = parts[1]   # e.g. "iron" → "iron_ingot"
        try:
            expected_count = int(parts[2])
        except ValueError:
            return None
        _SMELT_OUTPUT = {
            "iron": "iron_ingot", "raw_iron": "iron_ingot",
            "gold": "gold_ingot", "raw_gold": "gold_ingot",
            "copper": "copper_ingot", "raw_copper": "copper_ingot",
            "sand": "glass", "cobblestone": "stone",
        }
        output_item = _SMELT_OUTPUT.get(target_raw, target_raw)
        before_counts = _inventory_counts(before.get("inventory", []))
        after_counts  = _inventory_counts(after.get("inventory", []))
        gained = after_counts.get(output_item, 0) - before_counts.get(output_item, 0)
        if gained <= 0:
            return f"smelt 後 {output_item} 數量未增加（預期 +{expected_count}，實際 +{gained}）"
        if gained < expected_count:
            return f"smelt 只完成部分：{output_item} +{gained}（目標 {expected_count}）"

    elif verb == "mine" and len(parts) >= 2:
        target = parts[1]
        # Map ore name to item name (simplified)
        _ORE_TO_ITEM = {
            "diamond": "diamond", "iron": "raw_iron", "gold": "raw_gold",
            "coal": "coal", "copper": "raw_copper", "emerald": "emerald",
            "stone": "cobblestone", "gravel": "gravel", "sand": "sand",
        }
        item_name = _ORE_TO_ITEM.get(target, target)
        before_counts = _inventory_counts(before.get("inventory", []))
        after_counts  = _inventory_counts(after.get("inventory", []))
        gained = after_counts.get(item_name, 0) - before_counts.get(item_name, 0)
        if gained <= 0:
            return f"mine 後 {item_name} 數量未增加，可能採礦失敗或掉落物未撿"

    elif verb == "deposit":
        before_slots = (before.get("inventory_slots") or {}).get("used", 0)
        after_slots  = (after.get("inventory_slots") or {}).get("used", 0)
        if after_slots >= before_slots:
            return f"deposit 後背包槽位未減少（{before_slots} → {after_slots}）"

    return None


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
        self._latest_state: dict = {}   # updated every tick from agent.py
        self._before_state: dict = {}   # snapshot before each command
        self._after_state: dict = {}    # snapshot from action_done / activity_done
        self._command_sent_at: float = 0.0
        self._activity_retried: bool = False
        # Optional callback set by agent.py: async (state, ws) → None
        # When set, verification failures trigger LLM intervention instead of just logging.
        self._verify_failed_callback = None

    def heartbeat(self) -> None:
        """Called on every tick event to confirm JS is still alive."""
        self._last_heartbeat = asyncio.get_event_loop().time()

    def update_state(self, state: dict) -> None:
        """Called from agent.py on every state update so executor has current world state."""
        self._latest_state = state

    async def execute(self, commands: list, ws, goal: str = "", final_goal: str | None = None, resume_task: bool = False, preserve_task: bool = False, source: str | None = None) -> None:
        self._run_id += 1
        my_run_id = self._run_id
        self._running = True
        self._ws = ws
        self._replan_commands = None
        self._step_results = []
        self._context = {}

        if resume_task:
            task_memory.resume_interrupted(commands if commands else None, goal=goal or None)
        elif goal and not preserve_task:
            task_memory.save(goal, commands, final_goal=final_goal, source=source)

        print(f'[Executor] 開始執行計畫：{commands}')

        i = 0
        while i < len(commands):
            if not self._running:
                print('[Executor] 計畫已中止')
                if not preserve_task:
                    task_memory.mark_step_failed(i, "aborted")
                    task_memory.interrupt("aborted")
                break

            cmd_str = _substitute(commands[i], self._context)

            if preserve_task and cmd_str.strip() == 'resumetask':
                interrupted = task_memory.load()
                if not interrupted or interrupted.get('status') != 'interrupted':
                    print('[Executor] resumetask 失敗：找不到中斷中的任務')
                    task_memory.record_event(
                        'resumetask',
                        reason='missing_interrupted_task',
                        command='resumetask',
                        step=i,
                    )
                    break
                commands = interrupted.get('commands', [])
                i = interrupted.get('currentStep', 0)
                preserve_task = False
                task_memory.resume_interrupted()
                print(f'[Executor] 接回中斷任務，從步驟 {i} 繼續')
                continue

            if not preserve_task:
                task_memory.update_step(i)
                task_memory.mark_step_running(i)
            self._current_step_index = i

            msg = _parse(cmd_str)
            self._current_command = msg
            self._before_state = dict(self._latest_state)
            self._after_state = {}
            print(f'[Executor] 執行步驟 {i}: {cmd_str}')
            await ws.send(json.dumps(msg))
            self._command_sent_at = asyncio.get_event_loop().time()
            self._activity_retried = False

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
                        if not preserve_task:
                            task_memory.mark_step_failed(i, "timeout")
                        self._step_results.append({"cmd": cmd_str, "status": "failed", "error": "timeout"})
                        timed_out = True
                        break
                    elapsed = now - self._last_heartbeat
                    if elapsed > 10:
                        print(f'[Executor] 等待 "{cmd_str}" 中... (上次心跳 {elapsed:.0f}s 前)')

                    # Detect activity command that JS never started (command was dropped)
                    expected_activity = _cmd_to_activity(cmd_str)
                    if expected_activity and not self._activity_retried:
                        current_activity = self._latest_state.get('activity', 'idle')
                        idle_secs = now - self._command_sent_at
                        if current_activity == 'idle' and idle_secs > ACTIVITY_START_TIMEOUT:
                            print(f'[Executor] {expected_activity} 送出後 {idle_secs:.0f}s 仍未啟動，重試指令: {cmd_str}')
                            self._activity_retried = True
                            self._command_sent_at = now
                            await ws.send(json.dumps(msg))

                if not timed_out:
                    done_task.cancel()
                    stuck_task.cancel()

                # abort() increments _run_id — detect superseded run and bail out
                if self._run_id != my_run_id:
                    return

                if self._stuck_event.is_set():
                    # Once a step enters stuck recovery, the executor must not
                    # fall through to the old "normal completion" path. It has
                    # to wait for an explicit resume / skip / replan signal and
                    # then apply that control flow before touching the next step.
                    print(f'[Executor] 步驟 {i} 因 activity_stuck 暫停，等待 LLM 決策...')
                    await self._done.wait()

                    # abort() increments _run_id while unblocking _done
                    if self._run_id != my_run_id:
                        return

                    if self._skip_event.is_set():
                        # LLM decided this step is unrecoverable — skip it
                        print(f'[Executor] 步驟 {i} 被跳過: {cmd_str}')
                        if not preserve_task:
                            task_memory.mark_step_failed(i, "skipped")
                            task_memory.record_event(
                                'skip',
                                reason='activity_stuck_skip',
                                command=cmd_str,
                                step=i,
                                details={'source': 'activity_stuck'},
                            )
                        self._step_results.append({"cmd": cmd_str, "status": "failed", "error": "skipped"})
                        i += 1
                        self._current_command = None
                        continue

                    if self._replan_commands is not None:
                        new_cmds = self._replan_commands
                        self._replan_commands = None
                        print(f'[Executor] 接受重新規劃 step {i}: 舊剩餘={commands[i:]} → 新={new_cmds}')
                        if not preserve_task:
                            task_memory.mark_step_failed(i, "replanned")
                            task_memory.record_event(
                                'replan',
                                reason='activity_stuck_replan',
                                command=cmd_str,
                                step=i,
                                details={'new_commands': list(new_cmds)},
                            )
                        self._step_results.append({"cmd": cmd_str, "status": "replanned", "error": "replanned"})
                        if not preserve_task:
                            task_memory.replace_remaining_steps(i, new_cmds)
                        commands = commands[:i] + new_cmds
                        self._current_command = None
                        print(f'[Executor] 替換後完整計畫: {commands}，從步驟 {i} 繼續')
                        continue  # don't increment i — commands[i] is now the first new step

                    # Single-step recovery completed — verify before marking done.
                    warning = _verify_step(cmd_str, self._before_state, self._after_state)
                    if warning and self._verify_failed_callback and not preserve_task:
                        result = await self._handle_verify_failure(i, cmd_str, warning, commands, my_run_id, preserve_task)
                        if result == 'abort':
                            return
                        if result == 'skip':
                            i += 1
                            self._current_command = None
                            continue
                        if isinstance(result, list):
                            commands = commands[:i] + result
                            self._current_command = None
                            continue
                        # None → LLM accepted, fall through to mark done
                    elif warning:
                        print(f"[Executor] ⚠ 驗證警告 step {i} ({cmd_str}): {warning}")
                    if not preserve_task:
                        task_memory.mark_step_done(i)
                    self._step_results.append({"cmd": cmd_str, "status": "done", "warning": warning})
                else:
                    # Normal completion — verify before marking done.
                    warning = _verify_step(cmd_str, self._before_state, self._after_state)
                    if warning and self._verify_failed_callback and not preserve_task:
                        result = await self._handle_verify_failure(i, cmd_str, warning, commands, my_run_id, preserve_task)
                        if result == 'abort':
                            return
                        if result == 'skip':
                            i += 1
                            self._current_command = None
                            continue
                        if isinstance(result, list):
                            commands = commands[:i] + result
                            self._current_command = None
                            continue
                        # None → LLM accepted, fall through to mark done
                    elif warning:
                        print(f"[Executor] ⚠ 驗證警告 step {i} ({cmd_str}): {warning}")
                    if not preserve_task:
                        task_memory.mark_step_done(i)
                    self._step_results.append({"cmd": cmd_str, "status": "done", "warning": warning})

            except asyncio.CancelledError:
                if not preserve_task:
                    task_memory.mark_step_failed(i, "cancelled")
                    task_memory.interrupt("cancelled")
                self._step_results.append({"cmd": cmd_str, "status": "failed", "error": "cancelled"})
                break
            finally:
                self._current_command = None

            i += 1

        if self._running and not preserve_task:
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
            self._after_state = state

        event_type = (state or {}).get('type')
        command = self._current_command.get('command')

        immediate_commands = {
            'stopmine', 'stopchop', 'stopfish', 'stopsmelt', 'stopcombat', 'stophunt', 'stopgetfood', 'stopsurface', 'stopexplore',
            'home', 'back', 'sethome', 'equip', 'unequip', 'deposit', 'withdraw', 'readchest', 'setchest', 'labelchest',
            'makechest', 'chat', 'setmode', 'resumetask', 'tp',
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
            # else: some other recovery activity finished (e.g. surface during
            # chop step). Keep waiting — the original step must explicitly
            # finish, be skipped, or be replanned.

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

    async def _handle_verify_failure(
        self,
        i: int,
        cmd_str: str,
        warning: str,
        commands: list,
        my_run_id: int,
        preserve_task: bool,
    ) -> str | list | None:
        """
        Re-enter stuck recovery when post-action verification fails.
        Calls _verify_failed_callback (set by agent.py) with a synthetic activity_stuck state,
        then waits for LLM to decide replan / skip / accept.

        Returns:
            "abort"    — run was superseded, caller must return
            "skip"     — skip this step, caller increments i
            list       — new command list for replan, caller should splice into commands[:i]
            None       — LLM accepted step as done, caller marks it done normally
        """
        print(f'[Executor] ⚠ 驗證失敗 step {i} ({cmd_str}): {warning}，觸發 LLM 介入')

        # Re-enter stuck recovery so agent.py routes responses correctly
        self._in_stuck_recovery = True
        self._stuck_event.set()
        self._done.clear()
        self._skip_event.clear()
        self._replan_commands = None

        # Build synthetic activity_stuck state from latest after-state
        synthetic_state = {
            **self._after_state,
            'type': 'activity_stuck',
            'activity': _cmd_to_activity(cmd_str),
            'reason': 'verification_failed',
            'detail': warning,
            'verification_warning': warning,
        }
        # Inject plan_context (mirrors what agent.py does for real activity_stuck)
        task = task_memory.load()
        if task and task.get('steps'):
            idx = task.get('currentStep', 0)
            steps = task['steps']
            synthetic_state['plan_context'] = {
                'goal': task.get('goal'),
                'total_steps': len(steps),
                'current_step': idx,
                'current_cmd': steps[idx]['cmd'] if idx < len(steps) else None,
                'done_steps': [s['cmd'] for s in steps if s['status'] == 'done'],
                'pending_steps': [
                    s['cmd'] for s in steps[idx + 1:]
                    if s['status'] in ('pending', 'failed')
                ],
            }

        asyncio.create_task(self._verify_failed_callback(synthetic_state, self._ws))

        # Wait for LLM decision (replan / skip / resume)
        await self._done.wait()
        self._in_stuck_recovery = False

        if self._run_id != my_run_id:
            return 'abort'

        if self._skip_event.is_set():
            if not preserve_task:
                task_memory.mark_step_failed(i, 'verify_failed_skipped')
            self._step_results.append({'cmd': cmd_str, 'status': 'failed', 'error': 'verify_failed_skipped'})
            return 'skip'

        if self._replan_commands is not None:
            new_cmds = self._replan_commands
            self._replan_commands = None
            print(f'[Executor] 驗證失敗後重新規劃 step {i}: 舊剩餘={commands[i:]} → 新={new_cmds}')
            if not preserve_task:
                task_memory.mark_step_failed(i, 'verify_replanned')
                task_memory.replace_remaining_steps(i, new_cmds)
            self._step_results.append({'cmd': cmd_str, 'status': 'replanned', 'error': 'verify_replanned'})
            return new_cmds

        # LLM chose resume → accept step as done
        print(f'[Executor] LLM 接受驗證警告，步驟 {i} 視為完成')
        return None

    def abort(self, preserve_task: bool = False, reason: str = "aborted") -> None:
        if self._running:
            if preserve_task:
                task_memory.interrupt(reason)
            else:
                task_memory.mark_step_failed(self._current_step_index, "aborted")
                task_memory.interrupt(reason)
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

