from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import Config
from .epics import EpicDocument
from .utils import append_jsonl, atomic_write_json, now_iso


def state_path(feature_dir: Path) -> Path:
    return feature_dir / "state.json"


def events_path(feature_dir: Path) -> Path:
    return feature_dir / "events.jsonl"


def initial_state(feature_path: str, config: Config, epics: EpicDocument) -> dict[str, Any]:
    now = now_iso()
    return {
        "version": 1,
        "featureId": epics.feature.id,
        "featurePath": feature_path,
        "status": "not_started",
        "adapter": {"name": config.agent.adapter, "mode": config.agent.mode},
        "startedAt": None,
        "updatedAt": now,
        "currentEpic": None,
        "lastCompletedEpic": None,
        "epics": {epic.id: {"status": "pending", "attempts": 0} for epic in epics.epics},
        "summary": {"total": len(epics.epics), "complete": 0, "blocked": 0, "pending": len(epics.epics), "failed": 0},
    }


def load_state(feature_dir: Path, feature_path: str, config: Config, epics: EpicDocument) -> dict[str, Any]:
    path = state_path(feature_dir)
    if not path.exists():
        return initial_state(feature_path, config, epics)
    data = json.loads(path.read_text(encoding="utf-8"))
    for epic in epics.epics:
        data.setdefault("epics", {}).setdefault(epic.id, {"status": "pending", "attempts": 0})
    return data


def save_state(feature_dir: Path, state: dict[str, Any]) -> None:
    state["updatedAt"] = now_iso()
    state["summary"] = summarize(state)
    atomic_write_json(state_path(feature_dir), state)


def append_event(feature_dir: Path, event_type: str, **payload: Any) -> None:
    append_jsonl(events_path(feature_dir), {"time": now_iso(), "type": event_type, **payload})


def summarize(state: dict[str, Any]) -> dict[str, int]:
    counts = {"total": 0, "complete": 0, "blocked": 0, "pending": 0, "failed": 0}
    for item in state.get("epics", {}).values():
        counts["total"] += 1
        status = item.get("status", "pending")
        if status == "complete":
            counts["complete"] += 1
        elif status == "blocked":
            counts["blocked"] += 1
        elif status == "failed":
            counts["failed"] += 1
        else:
            counts["pending"] += 1
    return counts


def mark_feature_running(state: dict[str, Any]) -> None:
    if state.get("startedAt") is None:
        state["startedAt"] = now_iso()
    state["status"] = "running"


def reset_blocked_for_resume(state: dict[str, Any]) -> None:
    if state.get("status") in {"blocked", "interrupted"}:
        state["status"] = "running"
    for epic_state in state.get("epics", {}).values():
        if epic_state.get("status") == "blocked":
            epic_state["status"] = "pending"
            epic_state.pop("blocker", None)
            epic_state.pop("blockedAt", None)
