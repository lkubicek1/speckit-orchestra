from __future__ import annotations

from pathlib import Path

from speckit_orchestra.config import default_config
from speckit_orchestra.orchestrator import _changed_paths_since_status, _dirty_paths_for_run_preflight


def test_attempt_changes_ignore_baseline_and_runtime_artifacts(tmp_path: Path) -> None:
    config = default_config(tmp_path)
    before = """ M README.md
?? .spec-orchestra/features/001-demo/epics.yaml
?? .spec-orchestra/features/001-demo/runs/EPIC-001/attempt-001/prompt.md
"""
    after = before + """?? src/App.tsx
?? .spec-orchestra/features/001-demo/state.json
?? .spec-orchestra/features/001-demo/runs/EPIC-001/attempt-001/stdout.log
?? .spec-orchestra/features/001-demo/reports/summary.md
?? .spec-orchestra/features/001-demo/notes.md
"""

    changed = _changed_paths_since_status(before, after, config, "001-demo")

    assert changed == [".spec-orchestra/features/001-demo/notes.md", "src/App.tsx"]


def test_run_preflight_ignores_orchestra_project_artifacts(tmp_path: Path, monkeypatch) -> None:
    import speckit_orchestra.orchestrator as orchestrator

    config = default_config(tmp_path)
    monkeypatch.setattr(
        orchestrator.git,
        "changed_files",
        lambda root: [
            ".spec-orchestra/config.yaml",
            ".spec-orchestra/features/001-demo/epics.yaml",
            ".spec-orchestra/features/001-demo/state.json",
            "README.md",
        ],
    )

    assert _dirty_paths_for_run_preflight(tmp_path, config) == ["README.md"]
