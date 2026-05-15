from __future__ import annotations

from pathlib import Path

from speckit_orchestra.epics import Approval, Epic, Scope, Validation
from speckit_orchestra.feature import FeatureArtifacts, Task, artifact_relpaths, load_feature_artifacts
from speckit_orchestra.prompts import render_epic_prompt


def test_epic_prompt_includes_spec_kit_implementation_discipline(tmp_path: Path) -> None:
    feature_dir = tmp_path / "specs" / "001-demo"
    contracts_dir = feature_dir / "contracts"
    contracts_dir.mkdir(parents=True)
    artifacts = FeatureArtifacts(
        id="001-demo",
        path=feature_dir,
        spec=feature_dir / "spec.md",
        plan=feature_dir / "plan.md",
        tasks=feature_dir / "tasks.md",
        optional=(feature_dir / "research.md", contracts_dir / "api.md"),
    )
    epic = Epic(
        id="EPIC-001",
        title="Build widget",
        goal="Implement the widget flow.",
        tasks=["T001", "T002"],
        dependencies=[],
        risk="medium",
        parallelSafe=False,
        approval=Approval(required=False, reason=None),
        scope=Scope(include=["src/**", "tests/**"], exclude=["specs/**"]),
        acceptance=["Widget flow works."],
        validation=Validation(commands=["pytest"], manualChecks=[]),
        stopConditions=["Requirements conflict."],
    )
    tasks = [
        Task(id="T001", text="Write widget tests", line=10, section="Tests"),
        Task(id="T002", text="Implement widget", line=11, section="Core"),
        Task(id="T003", text="Implement later epic", line=12, section="Core"),
    ]

    prompt = render_epic_prompt(
        root=tmp_path,
        artifacts=artifacts,
        epic=epic,
        tasks=tasks,
        dependency_summary="",
    )

    assert "## Spec Kit Implementation Discipline" in prompt
    assert "current Spec Kit implementation workflow" in prompt
    assert "in-scope task IDs only" in prompt
    assert "Do not run interactive checklist prompts" in prompt
    assert "Do not mark tasks complete in `tasks.md`" in prompt
    assert "T003" not in prompt


def test_feature_artifacts_include_constitution_when_present(tmp_path: Path) -> None:
    feature_dir = tmp_path / "specs" / "001-demo"
    feature_dir.mkdir(parents=True)
    constitution = tmp_path / ".specify" / "memory" / "constitution.md"
    constitution.parent.mkdir(parents=True)
    constitution.write_text("# Constitution\n", encoding="utf-8")

    artifacts = load_feature_artifacts(tmp_path, "specs/001-demo")

    assert ".specify/memory/constitution.md" in artifact_relpaths(tmp_path, artifacts)
