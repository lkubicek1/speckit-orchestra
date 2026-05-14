from __future__ import annotations

from pathlib import Path

from .epics import EpicDocument
from .utils import atomic_write_text


def render_summary_report(doc: EpicDocument, state: dict[str, object]) -> str:
    epics_state = state.get("epics", {}) if isinstance(state.get("epics"), dict) else {}
    completed: list[str] = []
    blocked: list[str] = []
    pending: list[str] = []
    for epic in doc.epics:
        item = epics_state.get(epic.id, {}) if isinstance(epics_state, dict) else {}
        status = item.get("status", "pending") if isinstance(item, dict) else "pending"
        commit = item.get("commit") if isinstance(item, dict) else None
        line = f"{epic.id} {epic.title}" + (f" ({commit})" if commit else "")
        if status == "complete":
            completed.append(line)
        elif status == "blocked":
            blocker = item.get("blocker", {}) if isinstance(item, dict) else {}
            reason = blocker.get("message", "blocked") if isinstance(blocker, dict) else "blocked"
            blocked.append(f"{line}: {reason}")
        else:
            pending.append(line)

    next_action = "Run `speckit-orchestra run {}`.".format(doc.feature.path)
    if state.get("status") == "complete":
        next_action = "Feature orchestration is complete."
    elif blocked:
        next_action = "Resolve the blocker, then run `speckit-orchestra resume {}`.".format(doc.feature.path)

    return f"""# speckit-orchestra Summary

- Feature: {doc.feature.id}
- Status: {state.get('status', 'not_started')}
- Adapter: {state.get('adapter', {}).get('name', 'unknown') if isinstance(state.get('adapter'), dict) else 'unknown'} {state.get('adapter', {}).get('mode', '') if isinstance(state.get('adapter'), dict) else ''}
- Source: {doc.feature.path}

## Completed Epics

{_bullets(completed) if completed else "- None"}

## Blocked Epics

{_bullets(blocked) if blocked else "- None"}

## Pending Epics

{_bullets(pending) if pending else "- None"}

## Next Action

{next_action}
"""


def write_summary_report(feature_dir: Path, doc: EpicDocument, state: dict[str, object]) -> Path:
    path = feature_dir / "reports" / "summary.md"
    atomic_write_text(path, render_summary_report(doc, state))
    return path


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)
