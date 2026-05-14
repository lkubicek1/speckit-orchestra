from __future__ import annotations

import json
import os
import socket
from pathlib import Path

from .utils import atomic_write_json, now_iso


class LockError(RuntimeError):
    pass


def acquire_lock(path: Path, command: str, *, force: bool = False) -> None:
    if path.exists():
        data = _read_lock(path)
        pid = data.get("pid")
        hostname = data.get("hostname")
        if not force and hostname == socket.gethostname() and isinstance(pid, int) and _pid_alive(pid):
            raise LockError(f"active lock exists at {path} for pid {pid}")
        path.unlink(missing_ok=True)
    atomic_write_json(
        path,
        {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "command": command,
            "startedAt": now_iso(),
        },
    )


def release_lock(path: Path) -> None:
    path.unlink(missing_ok=True)


def _read_lock(path: Path) -> dict[str, object]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True
