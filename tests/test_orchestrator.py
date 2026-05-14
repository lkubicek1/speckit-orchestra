from __future__ import annotations

from pathlib import Path

from speckit_orchestra.config import default_config
from speckit_orchestra.orchestrator import (
    _changed_paths_since_snapshot,
    _changed_paths_since_status,
    _dirty_paths_for_run_preflight,
    _no_changes_blocker,
    _snapshot_status_paths,
)


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


def test_attempt_changes_detect_modified_already_dirty_file(tmp_path: Path) -> None:
    config = default_config(tmp_path)
    app = tmp_path / "src" / "App.tsx"
    app.parent.mkdir(parents=True)
    app.write_text("before\n", encoding="utf-8")
    status = " M src/App.tsx\n"
    snapshot = _snapshot_status_paths(tmp_path, config, "001-demo", status)

    app.write_text("after\n", encoding="utf-8")

    assert _changed_paths_since_snapshot(tmp_path, snapshot, status, config, "001-demo") == ["src/App.tsx"]


def test_attempt_changes_ignore_unchanged_already_dirty_file(tmp_path: Path) -> None:
    config = default_config(tmp_path)
    app = tmp_path / "src" / "App.tsx"
    app.parent.mkdir(parents=True)
    app.write_text("same\n", encoding="utf-8")
    status = " M src/App.tsx\n"
    snapshot = _snapshot_status_paths(tmp_path, config, "001-demo", status)

    assert _changed_paths_since_snapshot(tmp_path, snapshot, status, config, "001-demo") == []


def test_no_changes_blocker_preserves_validation_context_and_stdout(tmp_path: Path) -> None:
    attempt_dir = tmp_path / ".spec-orchestra" / "features" / "001-demo" / "runs" / "EPIC-001" / "attempt-002"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "stdout.log").write_text(
        "Agent finished.\n\nNo source changes made.\nValidation cannot pass without changing tests.\n",
        encoding="utf-8",
    )

    blocker = _no_changes_blocker(
        tmp_path,
        attempt_dir,
        "unit tests failed",
        [".spec-orchestra/features/001-demo/runs/EPIC-001/attempt-001/validation.log"],
    )

    assert blocker["category"] == "validation_failed"
    assert "without changing files" in blocker["message"]
    assert "No source changes made. Validation cannot pass without changing tests." in blocker["message"]
    assert blocker["evidence"] == [
        ".spec-orchestra/features/001-demo/runs/EPIC-001/attempt-001/validation.log",
        ".spec-orchestra/features/001-demo/runs/EPIC-001/attempt-002/stdout.log",
    ]


def test_no_changes_blocker_without_validation_stays_no_changes(tmp_path: Path) -> None:
    attempt_dir = tmp_path / ".spec-orchestra" / "features" / "001-demo" / "runs" / "EPIC-001" / "attempt-001"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "stdout.log").write_text("Nothing to update.\n", encoding="utf-8")

    blocker = _no_changes_blocker(tmp_path, attempt_dir)

    assert blocker["category"] == "no_changes"
    assert "Nothing to update." in blocker["message"]
    assert blocker["evidence"] == [".spec-orchestra/features/001-demo/runs/EPIC-001/attempt-001/stdout.log"]
