from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

from speckit_orchestra import git as git_utils
from speckit_orchestra.adapters import AgentInvocation, AgentRunResult
from speckit_orchestra.config import default_config
from speckit_orchestra.epics import Approval, Epic, EpicDocument, FeatureRef, Scope, Validation
from speckit_orchestra.orchestrator import (
    RunOptions,
    _changed_paths_since_snapshot,
    _changed_paths_since_status,
    _dirty_paths_for_run_preflight,
    _no_changes_blocker,
    _run_epic,
    _run_validation,
    _snapshot_status_paths,
    _scope_blocker,
)
from speckit_orchestra.state import initial_state


class _NoopAdapter:
    def __init__(self) -> None:
        self.calls = 0

    def build_invocation(self, config, root: Path, prompt: str) -> AgentInvocation:
        return AgentInvocation("fake-agent", [], root, prompt, 1000)

    def run(self, invocation: AgentInvocation, stdout_path: Path, stderr_path: Path) -> AgentRunResult:
        self.calls += 1
        stdout_path.write_text("All in-scope task IDs are satisfied.\nBlockers, if any\nNone.\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return AgentRunResult("complete", 0, "adapter exited successfully")


def _write_demo_feature(root: Path) -> None:
    feature_dir = root / "specs" / "001-demo"
    feature_dir.mkdir(parents=True)
    (feature_dir / "spec.md").write_text("# Demo\n", encoding="utf-8")
    (feature_dir / "plan.md").write_text("# Plan\n", encoding="utf-8")
    (feature_dir / "tasks.md").write_text("- [ ] T001 Implement demo artifact\n", encoding="utf-8")


def _demo_epic(validation_commands: list[str]) -> Epic:
    return Epic(
        id="EPIC-001",
        title="Build demo",
        goal="Implement the demo artifact.",
        tasks=["T001"],
        dependencies=[],
        risk="low",
        parallelSafe=False,
        approval=Approval(required=False, reason=None),
        scope=Scope(include=["src/**"], exclude=[]),
        acceptance=["Demo artifact exists."],
        validation=Validation(commands=validation_commands),
        stopConditions=["Requirements conflict."],
    )


def _demo_doc(epic: Epic) -> EpicDocument:
    return EpicDocument(
        feature=FeatureRef(
            id="001-demo",
            path="specs/001-demo",
            spec="specs/001-demo/spec.md",
            plan="specs/001-demo/plan.md",
            tasks="specs/001-demo/tasks.md",
        ),
        epics=[epic],
    )


def _python_command(source: str) -> str:
    return f"{shlex.quote(sys.executable)} -c {shlex.quote(source)}"


def _commit_initial(root: Path) -> None:
    git_utils.git(["init"], root)
    git_utils.git(["add", "."], root)
    git_utils.git(
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"],
        root,
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
        "specs/001-demo",
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
    assert "--allow-dirty" in blocker["suggestedNextAction"]


def test_no_changes_blocker_without_validation_stays_no_changes(tmp_path: Path) -> None:
    attempt_dir = tmp_path / ".spec-orchestra" / "features" / "001-demo" / "runs" / "EPIC-001" / "attempt-001"
    attempt_dir.mkdir(parents=True)
    (attempt_dir / "stdout.log").write_text("Nothing to update.\n", encoding="utf-8")

    blocker = _no_changes_blocker(tmp_path, "specs/001-demo", attempt_dir)

    assert blocker["category"] == "no_changes"
    assert "Nothing to update." in blocker["message"]
    assert blocker["evidence"] == [".spec-orchestra/features/001-demo/runs/EPIC-001/attempt-001/stdout.log"]


def test_run_epic_completes_no_diff_retry_with_preserved_changes(tmp_path: Path, monkeypatch) -> None:
    import speckit_orchestra.orchestrator as orchestrator

    _write_demo_feature(tmp_path)
    _commit_initial(tmp_path)
    config = default_config(tmp_path)
    config.agent.adapter = "fake"
    config.commit.mode = "never"
    epic = _demo_epic([_python_command("from pathlib import Path; assert Path('src/app.txt').exists()")])
    doc = _demo_doc(epic)
    state = initial_state("specs/001-demo", config, doc)
    state["epics"][epic.id]["attempts"] = 3
    feature_dir = tmp_path / config.project.orchestraRoot / "features" / "001-demo"
    previous_attempt = feature_dir / "runs" / epic.id / "attempt-001"
    previous_attempt.mkdir(parents=True)
    (previous_attempt / "changed-files.txt").write_text("src/app.txt\n", encoding="utf-8")
    source = tmp_path / "src" / "app.txt"
    source.parent.mkdir()
    source.write_text("implemented\n", encoding="utf-8")
    adapter = _NoopAdapter()
    monkeypatch.setattr(orchestrator, "get_adapter", lambda name: adapter)

    status = _run_epic(
        tmp_path,
        "specs/001-demo",
        config,
        doc,
        state,
        feature_dir,
        epic,
        RunOptions(),
        1,
        1,
    )

    result = json.loads(
        (feature_dir / "runs" / epic.id / "attempt-004" / "result.json").read_text(encoding="utf-8")
    )
    assert status == 0
    assert state["epics"][epic.id]["status"] == "complete"
    assert result["changedFiles"] == ["src/app.txt"]
    assert result["blocker"] is None
    assert adapter.calls == 1


def test_run_epic_blocks_no_diff_without_preserved_changes_after_validation(tmp_path: Path, monkeypatch) -> None:
    import speckit_orchestra.orchestrator as orchestrator

    _write_demo_feature(tmp_path)
    _commit_initial(tmp_path)
    config = default_config(tmp_path)
    config.agent.adapter = "fake"
    config.execution.maxRetries = 0
    epic = _demo_epic([_python_command("pass")])
    doc = _demo_doc(epic)
    state = initial_state("specs/001-demo", config, doc)
    feature_dir = tmp_path / config.project.orchestraRoot / "features" / "001-demo"
    adapter = _NoopAdapter()
    monkeypatch.setattr(orchestrator, "get_adapter", lambda name: adapter)

    status = _run_epic(
        tmp_path,
        "specs/001-demo",
        config,
        doc,
        state,
        feature_dir,
        epic,
        RunOptions(),
        1,
        1,
    )

    result = json.loads(
        (feature_dir / "runs" / epic.id / "attempt-001" / "result.json").read_text(encoding="utf-8")
    )
    assert status == 1
    assert state["epics"][epic.id]["status"] == "blocked"
    assert state["epics"][epic.id]["blocker"]["category"] == "no_changes"
    assert "exit code: 0" in result["validationSummary"]
    assert result["blocker"]["category"] == "no_changes"


def test_scope_blocker_can_be_disabled_by_config(tmp_path: Path) -> None:
    config = default_config(tmp_path)
    epic = Epic(
        id="EPIC-001",
        title="Scoped work",
        goal="Exercise scope config.",
        tasks=["T001"],
        dependencies=[],
        risk="low",
        parallelSafe=False,
        approval=Approval(required=False, reason=None),
        scope=Scope(include=["src/**"], exclude=["tests/**"]),
        acceptance=["Scope is checked."],
        validation=Validation(manualChecks=["Manual check."], expectedFailureAllowed=True),
        stopConditions=["Scope cannot be checked."],
    )

    assert _scope_blocker(epic, ["tests/example.test.ts"], config) is not None

    config.validation.blockOnForbiddenPaths = False

    assert _scope_blocker(epic, ["tests/example.test.ts"], config) is None


def test_diff_patch_can_be_limited_to_attempt_changed_files(tmp_path: Path) -> None:
    git_utils.git(["init"], tmp_path)
    (tmp_path / "kept.txt").write_text("before\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("before\n", encoding="utf-8")
    git_utils.git(["add", "kept.txt", "ignored.txt"], tmp_path)
    git_utils.git(
        ["-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "initial"],
        tmp_path,
    )

    (tmp_path / "kept.txt").write_text("after\n", encoding="utf-8")
    (tmp_path / "ignored.txt").write_text("after\n", encoding="utf-8")
    (tmp_path / "new.txt").write_text("new\n", encoding="utf-8")

    patch = git_utils.diff_patch(tmp_path, ["kept.txt", "new.txt"])

    assert "kept.txt" in patch
    assert "new.txt" in patch
    assert "ignored.txt" not in patch


def test_validation_command_times_out(tmp_path: Path) -> None:
    config = default_config(tmp_path)
    config.validation.commandTimeoutMs = 50
    attempt_dir = tmp_path / "attempt"
    epic = Epic(
        id="EPIC-001",
        title="Timeout validation",
        goal="Exercise validation timeout handling.",
        tasks=["T001"],
        dependencies=[],
        risk="low",
        parallelSafe=False,
        approval=Approval(required=False, reason=None),
        scope=Scope(include=["src/**"], exclude=[]),
        acceptance=["Validation timeout is reported."],
        validation=Validation(commands=[f"{shlex.quote(sys.executable)} -c 'import time; print(\"started\", flush=True); time.sleep(5)'"]),
        stopConditions=["Validation cannot complete."],
    )

    ok, summary = _run_validation(tmp_path, config, epic, attempt_dir, RunOptions())

    assert ok is False
    assert "started" in summary
    assert "timed out after 50ms" in summary
    assert "exit code:" in summary
    assert "timed out after 50ms" in (attempt_dir / "validation.log").read_text(encoding="utf-8")
