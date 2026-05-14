from __future__ import annotations

from pathlib import Path

from speckit_orchestra.config import default_config
from speckit_orchestra.refinement import generate_epic_document


def write_feature(root: Path) -> Path:
    feature = root / "specs" / "001-demo"
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text("# Demo\n", encoding="utf-8")
    (feature / "plan.md").write_text("# Plan\n", encoding="utf-8")
    (feature / "tasks.md").write_text(
        """# Tasks

## Data Model
- [ ] T001 Create src/models/user.py
- [ ] T002 Add tests/models/test_user.py

## API
- [ ] T003 Implement src/api/users.py
""",
        encoding="utf-8",
    )
    return feature


def test_generate_epics_preserves_tasks(tmp_path: Path) -> None:
    feature = write_feature(tmp_path)
    config = default_config(tmp_path)
    doc = generate_epic_document(tmp_path, str(feature), config)

    task_ids = [task for epic in doc.epics for task in epic.tasks]
    assert task_ids == ["T001", "T002", "T003"]
    assert doc.epics[1].dependencies == ["EPIC-001"]
    assert doc.feature.id == "001-demo"


def test_generated_scope_excludes_spec_artifacts(tmp_path: Path) -> None:
    feature = write_feature(tmp_path)
    config = default_config(tmp_path)
    doc = generate_epic_document(tmp_path, str(feature), config)

    assert "specs/001-demo/spec.md" in doc.epics[0].scope.exclude
    assert ".spec-orchestra/**" in doc.epics[0].scope.exclude
