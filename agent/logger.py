from __future__ import annotations

import os
import sys
from datetime import datetime


class _TeeStream:
    def __init__(self, original, file_handle, level: str):
        self._original = original
        self._file_handle = file_handle
        self._level = level

    def write(self, data: str) -> int:
        written = self._original.write(data)
        if data and not data.isspace():
            for line in data.splitlines(True):
                if not line.strip():
                    self._file_handle.write(line)
                    continue
                timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if line.endswith("\n"):
                    payload = line[:-1]
                    self._file_handle.write(f"[{timestamp}] [{self._level}] {payload}\n")
                else:
                    self._file_handle.write(f"[{timestamp}] [{self._level}] {line}")
        self._file_handle.flush()
        return written

    def flush(self) -> None:
        self._original.flush()
        self._file_handle.flush()

    def isatty(self) -> bool:
        return getattr(self._original, "isatty", lambda: False)()


def init_logger(name: str = "agent") -> str:
    if getattr(sys, "_agent_log_initialized", False):
        return getattr(sys, "_agent_log_path")

    base_dir = os.path.dirname(__file__)
    log_dir = os.path.join(base_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    log_path = os.path.join(log_dir, f"{name}-{stamp}.txt")
    file_handle = open(log_path, "a", encoding="utf-8", buffering=1)

    sys.stdout = _TeeStream(sys.stdout, file_handle, "INFO")
    sys.stderr = _TeeStream(sys.stderr, file_handle, "ERROR")
    sys._agent_log_initialized = True
    sys._agent_log_path = log_path
    print(f"[Log] 已寫入 {log_path}")
    return log_path
