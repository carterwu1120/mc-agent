import json

from agent.skills.state_summary import summary_json
from agent.skills.stuck import hunting as hunting_stuck
from agent.skills.stuck.prompts import REASON_DESC


def build_fishing_prompt(state: dict, health, food) -> str:
    pos = state.get("pos", {})
    water = state.get("waterTarget")
    map_data = state.get("areaMap")

    map_section = "（無地圖資料）"
    if isinstance(map_data, dict) and "grid" in map_data:
        grid = map_data["grid"]
        origin_x = map_data["originX"]
        origin_z = map_data["originZ"]
        x_labels = "     " + "".join(f"{origin_x + i:2d}" for i in range(len(grid[0])))
        rows = [x_labels]
        for i, row in enumerate(grid):
            z = origin_z + i
            rows.append(f"{z:4d}: {''.join(f' {c}' for c in row)}")
        map_section = "\n".join(rows)
    elif isinstance(map_data, str) and map_data:
        map_section = map_data

    reason = state.get("reason", "unknown")
    return (
        f"機器人在執行「fishing」時中斷（原因：{REASON_DESC.get(reason, reason)}）\n"
        f"當前狀態：位置 x={pos.get('x', '?'):.1f}, z={pos.get('z', '?'):.1f}，血量={health}/20，飢餓={food}/20\n"
        f"目標水面：{water}\n\n"
        f"周圍地形（B=Bot, W=水, .=可走, #=阻擋, ~=懸崖）：\n"
        f"{map_section}\n\n"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n"
    )


def build_activity_prompt(
    state: dict,
    activity: str,
    reason: str,
    inventory: list[dict],
    health,
    food,
    y: int,
    missing: list,
    needed_for: str | None,
    suggested_actions: list[str],
    detail: str | None,
    missing_count: int | None,
    plan_context: dict | None,
) -> tuple[str, list[str]]:
    remaining = state.get("remaining")
    inv_summary = "\n".join(f"- {i['name']} x{i['count']}" for i in inventory) or "（空背包）"
    reason_desc = REASON_DESC.get(reason, reason)

    extra_lines = []
    if missing:
        extra_lines.append(f"缺少資源/工具：{', '.join(missing)}")
    if needed_for:
        extra_lines.append(f"用途：{needed_for}")
    if suggested_actions:
        extra_lines.append(f"可考慮動作：{', '.join(suggested_actions)}")
    if missing_count is not None:
        extra_lines.append(f"缺少數量：{missing_count}")
    if state.get("craft_issue_suspected"):
        extra_lines.append("注意：目前看起來不是單純缺資源，而是 craft 流程可能異常失敗")
    if detail:
        extra_lines.append(f"補充說明：{detail}")
    if activity == "hunting" and reason == "no_animals":
        summary = state.get("summary") or {}
        raw_total = ((((summary.get("resources") or {}).get("food") or {}).get("raw_total", None)))
        if raw_total is None:
            from agent.skills.state_summary import summarize_state
            raw_total = int((((summarize_state(state).get("resources") or {}).get("food") or {}).get("raw_total", 0)) or 0)
        remaining_for_food = remaining if isinstance(remaining, int) and remaining > 0 else None
        if remaining_for_food is not None:
            threshold = min(8, max(1, (remaining_for_food + 1) // 2))
            extra_lines.append(
                f"生食判斷：raw_total={int(raw_total)}，先烹煮門檻={threshold}"
                "（當 raw_total >= 8 或 raw_total >= remaining * 0.5 時，優先先 getfood）"
            )
    if activity == "hunting" and reason == "no_weapon":
        route_info = hunting_stuck.describe_no_weapon_options(state, plan_context)
        extra_lines.append(f"武器缺口診斷：{', '.join(route_info['weapon_blockers']) or '（無明確材料缺口）'}")
        hints = route_info["environment_hints"]
        extra_lines.append(
            "環境提示："
            f" near_trees={hints['near_trees']},"
            f" near_stone={hints['near_stone']},"
            f" near_water={hints['near_water']}"
        )
        extra_lines.append(f"候選路線：{', '.join(route_info['candidate_routes'])}")
        extra_lines.append(
            "請只在候選路線中挑選最合理的一條；若熟食已足夠，優先跳過 hunt/getfood，"
            "不要再回 explore animals。"
        )
    extra = "\n".join(extra_lines)

    plan_section = ""
    pending_steps = []
    if plan_context:
        done = ", ".join(plan_context.get("done_steps", [])) or "（無）"
        pending_steps = plan_context.get("pending_steps", [])
        pending = ", ".join(pending_steps) or "（無）"
        plan_section = (
            f"\n【計畫進度】目標：{plan_context.get('goal', '?')}\n"
            f"共 {plan_context.get('total_steps', '?')} 步，"
            f"當前第 {plan_context.get('current_step', 0) + 1} 步：{plan_context.get('current_cmd', '?')}\n"
            f"已完成：{done}\n"
            f"待執行：{pending}\n"
        )
    remaining_note = f"\n還需熟食數量：{remaining} 個（請用此數字作為 getfood count）\n" if remaining is not None else ""
    pending_note = (
        f"\n原計畫剩餘步驟（replan 時必須附加在 hunt+getfood 之後）：{pending_steps}\n"
        if pending_steps else ""
    )

    chests_section = ""
    if activity == "makechest":
        chests = state.get("chests") or []
        if chests:
            chests_lines = "\n".join(
                f"- id={c['id']} label={c.get('label', '未分類')} freeSlots={c.get('freeSlots', '?')}"
                for c in chests
            )
            chests_section = f"\n已登記箱子：\n{chests_lines}\n"
        else:
            chests_section = "\n已登記箱子：（無）\n"

    stack_frames = state.get("stack") or []
    parent_frames = stack_frames[:-1] if len(stack_frames) > 1 else []
    parent_section = ""
    if parent_frames:
        parts = []
        for frame in parent_frames:
            name = frame.get("activity", "?")
            goal = frame.get("goal") or {}
            goal_str = json.dumps(goal, ensure_ascii=False) if goal else "無"
            parts.append(f"{name}（goal={goal_str}）")
        parent_section = f"\n【上層活動（觸發當前 {activity} 的背景任務）】\n" + " → ".join(parts) + "\n"

    prompt = (
        f"機器人在執行「{activity}」時中斷（原因：{reason_desc}）\n"
        f"當前狀態：位置 Y={y}，血量={health}/20，飢餓={food}/20\n\n"
        f"背包內容：\n{inv_summary}\n\n"
        f"{extra}"
        f"{parent_section}"
        f"{chests_section}"
        f"{remaining_note}"
        f"{pending_note}"
        f"{plan_section}\n"
        f"狀態摘要（JSON）：\n{summary_json(state)}\n\n"
        f"請決定機器人接下來要做什麼。"
    )
    return prompt, pending_steps
