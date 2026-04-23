import json
import os
import pathlib
import re
import uuid
from datetime import datetime, timezone, timedelta

import aiohttp
from agent.brain import LLMClient

_STALE_THRESHOLD = timedelta(seconds=30)

BOT_ID = os.environ.get("BOT_ID", "bot0")
DATA_ROOT = pathlib.Path(os.environ.get("BOT_DATA_DIR",
    str(pathlib.Path(__file__).parent.parent / "data" / "bot0"))).parent
COORDINATOR_URL = os.environ.get("COORDINATOR_URL", "http://localhost:3010")


SYSTEM_PROMPT = """你是多機器人 Minecraft 任務調度員。
玩家給你一個請求，你需要根據每個機器人的當前狀態（血量、飢餓、背包、當前任務、近期失敗）決定任務分配。

每個機器人可以被分配獨立的 commands 序列。

只能回覆以下 JSON（不加任何其他文字）：
{"assignments": [{"bot_id": "bot0", "goal": "簡短目標", "commands": ["cmd1", "cmd2"], "interrupt": false}, ...], "aborts": ["bot0", "bot1"], "text": "給玩家的說明"}

- aborts：要強制停止的機器人 bot_id 列表。停止不需要 commands，直接列在 aborts 裡
- 玩家說「停止」「暫停」「全部停止」「全部暫停」「abort」「pause」「stop」等明確停止/暫停指令時，使用 aborts 而非 assignments
- 停止和暫停效果相同：中斷任務並保留進度，玩家之後可以用 !resume 恢復

【決策原則】
- 優先讓空閒且狀態良好的機器人接任務
- 若機器人 food < 8，優先讓他先 getfood count 8，或跳過分配
- commands 必須是合法指令（mine, chop, hunt, fish, explore, equip, smelt, getfood, idle 等）
- 禁止使用 craft 指令，它不存在。需要工具時用 equip（會自動製作）；需要熟食時用 getfood
- 多機器人分工只在 command 類型真的不同時才適用（mine + chop = OK；hunt + hunt = 絕對不行；getfood + getfood = 絕對不行）
- 同一個 command 類型（hunt、getfood、mine、chop、fish 等）只能分配給一個機器人，數量不同也算同一種類
- 「兩組食物」「三組木頭」這類數量請求，只指派給一個機器人，讓那個機器人自己完成全部數量
- 若所有機器人都正在執行任務（非 self_task），assignments 回傳空陣列，並在 text 告知玩家所有機器人正忙，無法接受新任務
- assignments 可以是空陣列（若判斷所有機器人都不適合接任務）
- text 欄位必須如實反映 assignments 內容，不可說「已指派 botX」如果 botX 不在 assignments 裡
- text 只描述「現在正在做什麼」，不預測未來結果。不可說「總計已滿足需求」，因為任務尚未完成

【中斷決策原則（interrupt 欄位）】
- source=idle（無 current_task）：直接分配，interrupt=false
- source=self_task：機器人閒置 60 秒後自主決定的任務，可設 interrupt=true 來搶佔
- source=player：玩家明確指定的任務，interrupt 必須是 false
- source=coordinator：協調員指派的任務，interrupt 必須是 false
- source=unknown：保守處理，interrupt=false
- 若所有機器人皆為 player/coordinator source，告知玩家目前無空閒機器人
"""


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
    print(f"[Coordinator] Prompt:\n{prompt}\n---")

    try:
        response = await llm.chat([{"role": "user", "content": prompt}], system=SYSTEM_PROMPT)
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
