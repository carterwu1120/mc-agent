import json
import os
import pathlib
import re
import uuid
from datetime import datetime, timezone, timedelta

import aiohttp
from agent.brain import LLMClient
from agent.prompt_logging import log_prompt
from agent.handlers.prompts import render_prompt

_STALE_THRESHOLD = timedelta(seconds=30)

BOT_ID = os.environ.get("BOT_ID", "bot0")
DATA_ROOT = pathlib.Path(os.environ.get("BOT_DATA_DIR",
    str(pathlib.Path(__file__).parent.parent / "data" / "bot0"))).parent
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "http://localhost:3010")


SYSTEM_PROMPT = render_prompt("coordinator")


def _collect_all_bots_state() -> list[dict]:
    bots = []
    for live_file in sorted(DATA_ROOT.glob("*/live_state.json")):
        bid = live_file.parent.name
        try:
            snap = json.loads(live_file.read_text(encoding="utf-8"))
            if not snap.get("ws_connected"):
                continue
            updated_at = snap.get("updated_at")
            if updated_at:
                try:
                    age = datetime.now(timezone.utc) - datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                    if age > _STALE_THRESHOLD:
                        continue  # stale snapshot — bot likely offline
                except Exception:
                    pass
            task = _load_json(DATA_ROOT / bid / "task.json")
            bots.append({
                "bot_id": bid,
                "activity": snap.get("activity"),
                "health": snap.get("health"),
                "food": snap.get("food"),
                "pos": snap.get("pos"),
                "inventory": snap.get("inventory") or [],
                "equipment": snap.get("equipment") or {},
                "current_task": task.get("goal") if task else None,
                "task_status": task.get("status") if task else None,
                "task_source": task.get("source") if task else None,
                "recent_failures": (task.get("recentFailures") or [])[:3] if task else [],
            })
        except Exception:
            pass
    return bots


def _build_prompt(request: str, bots: list[dict]) -> str:
    bots_text = ""
    for b in bots:
        inv = ", ".join(f"{i['name']}x{i['count']}" for i in b["inventory"][:10]) or "（空）"
        bots_text += (
            f"\n【{b['bot_id']}】activity={b['activity']} health={b['health']} food={b['food']}\n"
            f"  inventory: {inv}\n"
            f"  current_task: {b['current_task'] or '（無）'} ({b['task_status'] or '-'}, source={b.get('task_source') or 'unknown'})\n"
        )
        if b["recent_failures"]:
            fails = ", ".join(f.get("reason", "?") for f in b["recent_failures"])
            bots_text += f"  recent_failures: {fails}\n"
    return (
        f"玩家請求：{request}\n\n"
        f"目前所有機器人狀態：{bots_text}\n"
        "請根據以上狀態，決定如何分配這個任務。"
    )


async def handle(state: dict, llm: LLMClient, request: str) -> list | None:
    bots = _collect_all_bots_state()
    if not bots:
        return [{"command": "chat", "text": "目前無法讀取機器人狀態。"}]

    prompt = _build_prompt(request, bots)
    print(f"[Coordinator] 調度請求: {request}")
    log_prompt("Coordinator", prompt)

    try:
        response = await llm.chat(
            [{"role": "user", "content": prompt}],
            system=SYSTEM_PROMPT,
        )
        clean = re.sub(r"<think>.*?</think>", "", response, flags=re.DOTALL).strip()
        clean = re.sub(r"^```[a-z]*\n?", "", clean).rstrip("`").strip()
        decision = json.loads(clean)
    except Exception as e:
        print(f"[Coordinator] LLM 解析失敗: {e}")
        return [{"command": "chat", "text": "調度決策失敗，請稍後再試。"}]

    assignments = decision.get("assignments") or []
    aborts = decision.get("aborts") or []
    reply_text = decision.get("text", "")
    result = []

    if reply_text:
        result.append({"command": "chat", "text": reply_text})

    for bid in aborts:
        if bid == BOT_ID:
            result.append({"action": "abort_self"})
        else:
            await _abort_bot(bid)

    own_assignment = None
    for a in assignments:
        bid = a.get("bot_id")
        cmds = a.get("commands") or []
        goal = a.get("goal", request)
        interrupt = bool(a.get("interrupt", False))
        if not cmds:
            continue
        if bid == BOT_ID:
            own_assignment = {"action": "plan", "commands": cmds, "goal": goal}
        else:
            await _dispatch_to_bot(bid, cmds, goal, interrupt=interrupt)

    if own_assignment:
        result.append(own_assignment)

    return result or None


async def _abort_bot(bot_id: str) -> None:
    try:
        async with aiohttp.ClientSession() as s:
            resp = await s.post(f"{COORDINATOR_URL}/bots/{bot_id}/abort")
            if resp.status != 200:
                body = await resp.text()
                print(f"[Coordinator] abort {bot_id} 失敗: HTTP {resp.status} {body}")
                return
    except Exception as e:
        print(f"[Coordinator] abort {bot_id} 失敗: {e}")
        return
    print(f"[Coordinator] 已發送 abort 至 {bot_id}")


async def _dispatch_to_bot(bot_id: str, commands: list[str], goal: str, interrupt: bool = False) -> None:
    task_id = uuid.uuid4().hex[:12]
    try:
        async with aiohttp.ClientSession() as s:
            resp = await s.post(
                f"{COORDINATOR_URL}/bots/{bot_id}/tasks",
                json={"task_id": task_id, "commands": commands, "goal": goal, "interrupt": interrupt},
            )
            if resp.status not in (200, 201):
                body = await resp.text()
                print(f"[Coordinator] 指派 {bot_id} 失敗: HTTP {resp.status} {body}")
                return
    except Exception as e:
        print(f"[Coordinator] 指派 {bot_id} 失敗: {e}")
        return
    print(f"[Coordinator] 指派 {bot_id}: {commands} (task_id={task_id})")


def _load_json(path: pathlib.Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
    except Exception:
        return None
