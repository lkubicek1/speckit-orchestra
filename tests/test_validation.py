from __future__ import annotations

from pathlib import Path

from speckit_orchestra.config import default_config
from speckit_orchestra.epics import write_epics
from speckit_orchestra.refinement import generate_epic_document
from speckit_orchestra.validation import epics_path, validate_feature


def make_feature(root: Path) -> Path:
    feature = root / "specs" / "001-demo"
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text("# Spec\n", encoding="utf-8")
    (feature / "plan.md").write_text("# Plan\n", encoding="utf-8")
    (feature / "tasks.md").write_text(
        """# Tasks

- [ ] T001 Create thing
- [ ] T002 Test thing
""",
        encoding="utf-8",
    )
    return feature


def test_validate_generated_epics(tmp_path: Path) -> None:
    feature = make_feature(tmp_path)
    config = default_config(tmp_path)
    doc = generate_epic_document(tmp_path, str(feature), config)
    write_epics(epics_path(tmp_path, config, "001-demo"), doc)

    report = validate_feature(tmp_path, str(feature), config, check_git=False)

    assert report.errors == []


def test_validate_detects_missing_task(tmp_path: Path) -> None:
    feature = make_feature(tmp_path)
    config = default_config(tmp_path)
    doc = generate_epic_document(tmp_path, str(feature), config)
    doc.epics[0].tasks = ["T001"]
    write_epics(epics_path(tmp_path, config, "001-demo"), doc)

    report = validate_feature(tmp_path, str(feature), config, check_git=False)

    assert any("T002" in error for error in report.errors)
