# Backend API Contract

This document is the canonical response contract for the current public backend API on `:8000`.

Rules:
- Field names use `snake_case`.
- Clients should treat fields documented here as stable for the current MVP.
- Coordinator routes on `:3010` are internal and are not part of this contract.
- Dashboard UI on `:3002` should consume these backend routes instead of reading bot files directly.

## Common Notes

### Task Status Values

`task.status` may currently be one of:

- `queued`
- `running`
- `done`
- `failed`
- `interrupted`
- `unknown`

### Time Fields

Time fields are returned as strings. Current examples use ISO-8601 timestamps such as:

```json
"2026-05-12T16:25:57Z"
```

Some task-local timestamps may still reflect legacy agent formatting without a trailing `Z`. Clients should treat them as opaque timestamp strings for now.

### Position Shape

When position is available, it is returned as:

```json
{ "x": 22.0, "y": 73.0, "z": -123.0 }
```

## Health Endpoints

### `GET /health`

Process liveness only.

| Field | Type | Notes |
|---|---|---|
| `ok` | `bool` | Always `true` on success |
| `service` | `string` | Currently `backend` |

Example:

```json
{
  "ok": true,
  "service": "backend"
}
```

### `GET /ready`

Backend readiness. This includes coordinator reachability.

Success example:

```json
{
  "ok": true,
  "service": "backend",
  "coordinator": true
}
```

Failure behavior:
- Returns HTTP `503`
- Error payload is in `detail`

Failure example:

```json
{
  "detail": {
    "ok": false,
    "service": "backend",
    "coordinator": false,
    "reason": "coordinator offline"
  }
}
```

## Bot Endpoints

### `GET /bots`

Returns bot summaries.

Response type:

```json
[
  {
    "bot_id": "bot0",
    "name": "Agent0",
    "online": true,
    "activity": "idle",
    "position": { "x": 22.0, "y": 73.0, "z": -123.0 },
    "health": 20,
    "food": 20,
    "current_task": "mine iron 8",
    "queued_count": 0,
    "last_seen": "2026-05-12T16:25:57Z",
    "source": "coordinator"
  }
]
```

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `bot_id` | `string` | No | Canonical bot identifier |
| `name` | `string` | No | Display name from snapshot, falls back to `bot_id` |
| `online` | `bool` | No | Backend-normalized online state |
| `activity` | `string` | Yes | Current activity if known |
| `position` | `object` | Yes | `{x, y, z}` when known |
| `health` | `number` | Yes | Current health if known |
| `food` | `number` | Yes | Current food if known |
| `current_task` | `string` | Yes | Current task goal summary |
| `queued_count` | `integer` | No | Coordinator queue length |
| `last_seen` | `string` | Yes | Last heartbeat or snapshot timestamp |
| `source` | `string` | Yes | Current task source when available |

### `GET /bots/{bot_id}/state`

Returns the canonical detailed state model for one bot.

Top-level fields:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `bot_id` | `string` | No | Canonical bot identifier |
| `name` | `string` | No | Display name |
| `online` | `bool` | No | Backend-normalized online state |
| `last_seen` | `string` | Yes | Last heartbeat or snapshot timestamp |
| `status` | `object` | No | Current bot status block |
| `current_task` | `object` | Yes | Current task summary |
| `interrupted_tasks` | `array<object>` | No | Recently interrupted task summaries |
| `equipment` | `object` | No | Equipment snapshot |
| `inventory` | `array<object>` | No | Top inventory items |
| `inventory_slots` | `object` | No | Slot usage summary |
| `chests` | `array<object>` | No | Known chest summaries |
| `recent_events` | `array<object>` | No | Most recent events |
| `recent_failures` | `array<object>` | No | Most recent failures |
| `coordinator` | `object` | No | Coordinator runtime flags |

`status` object:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `activity` | `string` | Yes | Current activity if online |
| `position` | `object` | Yes | `{x, y, z}` when known |
| `health` | `number` | Yes | Health if known |
| `food` | `number` | Yes | Food if known |
| `mode` | `string` | Yes | Game mode if known |
| `home` | `object \| string \| null` | Yes | Current home marker if configured |

`current_task` object:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | `string` | Yes | Task identifier |
| `goal` | `string` | Yes | Task goal |
| `final_goal` | `string` | Yes | Final normalized goal if present |
| `status` | `string` | Yes | Task status |
| `interruptedBy` | `string` | Yes | Interrupt reason/source |
| `createdAt` | `string` | Yes | Creation timestamp |
| `currentStep` | `integer` | No | Current step index |
| `totalSteps` | `integer` | No | Total planned steps |
| `currentCmd` | `string` | Yes | Current command being executed |
| `pendingSteps` | `array<string>` | No | Remaining relevant commands |
| `progress_pct` | `integer` | No | Approximate progress percentage |
| `source` | `string` | Yes | Task source such as `coordinator` or `self_task` |
| `goalVerified` | `bool` | Yes | Goal-verification result if known |

`coordinator` object:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `online` | `bool` | No | Coordinator view of bot liveness |
| `queued_count` | `integer` | No | Queue length |
| `running_task_ids` | `array<string>` | No | Runtime task ids still running |
| `interrupt_task_id` | `string` | Yes | Pending interrupt task id |
| `abort_flag` | `bool` | No | Abort flag currently set in coordinator |

Example:

```json
{
  "bot_id": "bot0",
  "name": "bot0",
  "online": true,
  "last_seen": "2026-05-12T16:25:57Z",
  "status": {
    "activity": "idle",
    "position": { "x": 22.0, "y": 73.0, "z": -123.0 },
    "health": 20,
    "food": 20,
    "mode": "survival",
    "home": null
  },
  "current_task": {
    "id": "781883aca6f7",
    "goal": "mine iron 8",
    "final_goal": null,
    "status": "done",
    "interruptedBy": null,
    "createdAt": "2026-05-12T16:24:09.793958",
    "currentStep": 0,
    "totalSteps": 1,
    "currentCmd": "mine iron 8",
    "pendingSteps": [],
    "progress_pct": 0,
    "source": "coordinator",
    "goalVerified": true
  },
  "interrupted_tasks": [],
  "equipment": {},
  "inventory": [
    { "name": "raw_iron", "count": 15 }
  ],
  "inventory_slots": {
    "used": 32,
    "total": 36,
    "free": 4
  },
  "chests": [],
  "recent_events": [],
  "recent_failures": [],
  "coordinator": {
    "online": true,
    "queued_count": 0,
    "running_task_ids": [],
    "interrupt_task_id": null,
    "abort_flag": false
  }
}
```

### `GET /bots/{bot_id}/tasks`

Runtime queue view only. This endpoint is not historical reporting.

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `bot_id` | `string` | No | Canonical bot identifier |
| `online` | `bool` | No | Coordinator online state |
| `last_seen` | `string` | Yes | Last heartbeat timestamp |
| `running` | `array<object>` | No | Runtime tasks currently running |
| `queued` | `array<object>` | No | Runtime tasks queued but not started |
| `interrupt` | `object` | Yes | Pending interrupt task |
| `abort_flag` | `bool` | No | Current abort flag |

Task objects in `running`, `queued`, and `interrupt` currently follow:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `task_id` | `string` | No | Task identifier |
| `bot_id` | `string` | No | Bot owner |
| `goal` | `string` | Yes | Goal text |
| `commands` | `array<string>` | Yes | Structured commands |
| `status` | `string` | Yes | Runtime status |
| `interrupt` | `bool` | Yes | Whether task was submitted as an interrupt |

Example:

```json
{
  "bot_id": "bot0",
  "online": true,
  "last_seen": "2026-05-12T16:24:24Z",
  "running": [
    {
      "task_id": "781883aca6f7",
      "bot_id": "bot0",
      "commands": ["mine iron 8"],
      "goal": "mine iron 8",
      "status": "running",
      "interrupt": false
    }
  ],
  "queued": [],
  "interrupt": null,
  "abort_flag": false
}
```

## Task Endpoints

### `POST /bots/{bot_id}/tasks`

Structured task submission. This is commands-only in the current MVP.

Request body:

| Field | Type | Required | Notes |
|---|---|---|---|
| `goal` | `string` | Yes | Non-empty task goal |
| `commands` | `array<string>` | Yes | At least one non-empty command |
| `interrupt` | `bool` | No | Defaults to `false` |
| `task_id` | `string` | No | Client-provided idempotency key |

Request example:

```json
{
  "goal": "mine iron 8",
  "commands": ["equip pickaxe", "mine iron 8"],
  "interrupt": false
}
```

Response body:

| Field | Type | Notes |
|---|---|---|
| `task_id` | `string` | Generated if client did not provide one |
| `bot_id` | `string` | Target bot |
| `status` | `string` | `queued` or `already_queued` |

Response example:

```json
{
  "task_id": "781883aca6f7",
  "bot_id": "bot0",
  "status": "queued"
}
```

Status codes:
- `202`: newly queued
- `200`: duplicate `task_id`, existing task retained
- `404`: bot not registered
- `422`: invalid request body

### `GET /tasks/{task_id}`

Task lookup across runtime and archived history.

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `task_id` | `string` | No | Task identifier |
| `bot_id` | `string` | Yes | Bot owner if known |
| `goal` | `string` | Yes | Goal text |
| `commands` | `array<string>` | Yes | Structured commands when known |
| `status` | `string` | No | Runtime or archived task status |
| `source` | `string` | No | `runtime` or `history` |

Runtime example:

```json
{
  "task_id": "781883aca6f7",
  "bot_id": "bot0",
  "goal": "mine iron 8",
  "commands": ["mine iron 8"],
  "status": "running",
  "source": "runtime"
}
```

Archived example:

```json
{
  "task_id": "hist-task",
  "bot_id": "bot0",
  "goal": "mine diamond 3",
  "commands": ["mine diamond 3"],
  "status": "done",
  "source": "history"
}
```

## Metrics Endpoints

All metrics endpoints accept an optional query parameter:

| Parameter | Type | Default | Notes |
|---|---|---|---|
| `hours` | `integer` | `24` | Range `1..168` |

### `GET /metrics/success-rate`

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `since_hours` | `integer` | No | Effective lookback window |
| `task_success_rate` | `number` | Yes | Fraction of tasks that ended successfully |
| `goal_success_rate` | `number` | Yes | Fraction of goal-verified tasks |
| `tasks` | `object` | No | Aggregate task counters |

Example:

```json
{
  "since_hours": 24,
  "task_success_rate": 1.0,
  "goal_success_rate": 1.0,
  "tasks": {
    "done": 3,
    "failed": 0,
    "total": 3
  }
}
```

### `GET /metrics/stuck-count`

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `since_hours` | `integer` | No | Effective lookback window |
| `total` | `integer` | No | Sum of all stuck reasons |
| `by_reason` | `object` | No | Counts keyed by stuck reason |
| `by_activity` | `object` | No | Counts keyed by activity |

Example:

```json
{
  "since_hours": 24,
  "total": 2,
  "by_reason": {
    "no_tools": 1,
    "hostile_mobs": 1
  },
  "by_activity": {
    "mining": 2
  }
}
```

### `GET /metrics/llm-latency`

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `since_hours` | `integer` | No | Effective lookback window |
| `count` | `integer` | No | Number of LLM calls included |
| `avg_ms` | `number` | Yes | Average latency in milliseconds |
| `p95_ms` | `number` | Yes | Approximate p95 latency in milliseconds |
| `by_skill` | `object` | No | Aggregate stats keyed by skill name |

Example:

```json
{
  "since_hours": 24,
  "count": 8,
  "avg_ms": 1100.5,
  "p95_ms": 1800.0,
  "by_skill": {
    "planner": {
      "count": 5,
      "avg_ms": 1200.0,
      "p95_ms": 1800.0
    }
  }
}
```
