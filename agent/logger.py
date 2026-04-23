from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone

_current_task_id: str | None = None


def set_task_id(tid: str | None) -> None:
    global _current_task_id
    _current_task_id = tid


def get_task_id() -> str | None:
    return _current_task_id


class _TeeStream:
    def __init__(self, original, file_handle, level: str, service: str, bot_id: str):
        self._original = original
        self._file_handle = file_handle
        self._level = level
        self._service = service
        self._bot_id = bot_id

    def write(self, data: str) -> int:
        written = self._original.write(data)
        if data and not data.isspace():
            for line in data.splitlines(True):
                if not line.strip():
                    self._file_handle.write("\n")
                    continue
                msg = line.rstrip("\n")
                # [State] tick lines are terminal-only — too noisy for log file
                if msg.lstrip().startswith("[State]"):
                    continue
                entry = {
                    "time": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
                    "level": self._level,
                    "service": self._service,
                    "bot_id": self._bot_id,
                    "task_id": _current_task_id,
                    "msg": msg,
                }
                self._file_handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                try:
                    import agent.history_db as _hdb
                    _hdb._fire_and_forget(_hdb.write_log, entry)
                except Exception:
                    pass
        self._file_handle.flush()
        return written

    def flush(self) -> None:
        self._original.flush()
        self._file_handle.flush()

    def isatty(self) -> bool:
        return getattr(self._original, "isatty", lambda: False)()


def init_logger(service: str = "agent") -> str:
    if getattr(sys, "_agent_log_initialized", False):
        return getattr(sys, "_agent_log_path")

    bot_id = _resolve_bot_id()
    base_dir = os.path.dirname(__file__)
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    bot_label = _resolve_log_label()
    filename = f"{service}-{bot_label}-{stamp}.jsonl" if bot_label else f"{service}-{stamp}.jsonl"
    log_path = os.path.join(log_dir, filename)
    file_handle = open(log_path, "a", encoding="utf-8", buffering=1)

    sys.stdout = _TeeStream(sys.stdout, file_handle, "INFO", service, bot_id)
    sys.stderr = _TeeStream(sys.stderr, file_handle, "ERROR", service, bot_id)
    sys._agent_log_initialized = True
    sys._agent_log_path = log_path
    print(f"[Log] 已寫入 {log_path}")
    return log_path


def _resolve_bot_id() -> str:
    return (os.getenv("BOT_ID") or "").strip() or "bot0"


def _resolve_log_label() -> str | None:
    bot_id = (os.getenv("BOT_ID") or "").strip()
    if bot_id:
        return _sanitize_label(bot_id)

    data_dir = (os.getenv("BOT_DATA_DIR") or "").strip()
    if data_dir:
        base = os.path.basename(os.path.normpath(data_dir))
        if re.fullmatch(r"bot\d+", base, flags=re.IGNORECASE):
            return _sanitize_label(base)

    mc_username = (os.getenv("MC_USERNAME") or "").strip()
    if re.fullmatch(r"Agent\d+", mc_username, flags=re.IGNORECASE):
        return _sanitize_label(mc_username)
    return None


def _sanitize_label(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", value.strip())
    return cleaned.strip("-_") or "bot"
