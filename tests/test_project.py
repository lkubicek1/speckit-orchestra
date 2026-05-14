from __future__ import annotations

from pathlib import Path

from speckit_orchestra.project import clean_project, ensure_git_info_exclude, remove_git_info_exclude


def test_git_info_exclude_is_managed_locally(tmp_path: Path) -> None:
    (tmp_path / ".git" / "info").mkdir(parents=True)
    exclude = tmp_path / ".git" / "info" / "exclude"
    exclude.write_text("*.log\n", encoding="utf-8")

    assert ensure_git_info_exclude(tmp_path, ".spec-orchestra") is True
    assert "/.spec-orchestra/features/*/runs/" in exclude.read_text(encoding="utf-8")
    assert ensure_git_info_exclude(tmp_path, ".spec-orchestra") is False

    assert remove_git_info_exclude(tmp_path) is True
    assert exclude.read_text(encoding="utf-8") == "*.log\n"


def test_clean_project_removes_full_orchestra_root_and_excludes(tmp_path: Path) -> None:
    (tmp_path / ".git" / "info").mkdir(parents=True)
    (tmp_path / ".spec-orchestra" / "features" / "001-demo").mkdir(parents=True)
    (tmp_path / ".spec-orchestra" / "config.yaml").write_text("version: 2\n", encoding="utf-8")
    ensure_git_info_exclude(tmp_path, ".spec-orchestra")

    result = clean_project(tmp_path)

    assert result.ok
    assert tmp_path / ".spec-orchestra" in result.removed
    assert not (tmp_path / ".spec-orchestra").exists()
    assert result.updated_exclude is True


def test_clean_project_runtime_only_keeps_config_and_epics(tmp_path: Path) -> None:
    feature_dir = tmp_path / ".spec-orchestra" / "features" / "001-demo"
    (feature_dir / "runs" / "EPIC-001").mkdir(parents=True)
    (feature_dir / "reports").mkdir()
    (feature_dir / "state.json").write_text("{}\n", encoding="utf-8")
    (feature_dir / "epics.yaml").write_text("version: 1\n", encoding="utf-8")
    (tmp_path / ".spec-orchestra" / "config.yaml").write_text("version: 2\n", encoding="utf-8")

    result = clean_project(tmp_path, runtime_only=True)

    assert result.ok
    assert not (feature_dir / "runs").exists()
    assert not (feature_dir / "reports").exists()
    assert not (feature_dir / "state.json").exists()
    assert (feature_dir / "epics.yaml").exists()
    assert (tmp_path / ".spec-orchestra" / "config.yaml").exists()
