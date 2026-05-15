from __future__ import annotations

import tomllib
from argparse import Namespace
from pathlib import Path

import pytest

import speckit_orchestra
from speckit_orchestra.cli import _is_newer_version, _menu_lines, _path_version_checks, _resolve_feature_arg, _version_doctor_checks, build_parser
from speckit_orchestra.config import default_config, load_config, write_config
from speckit_orchestra.feature import discover_feature_paths


def make_feature(root: Path, name: str = "001-demo") -> Path:
    feature = root / "specs" / name
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text("# Spec\n", encoding="utf-8")
    return feature


def test_feature_arguments_are_optional() -> None:
    parser = build_parser()

    assert parser.parse_args(["refine"]).feature is None
    assert parser.parse_args(["validate"]).feature is None
    assert parser.parse_args(["run"]).feature is None
    assert parser.parse_args(["resume"]).feature is None
    assert parser.parse_args(["status"]).feature is None
    assert parser.parse_args(["report"]).feature is None


def test_run_still_accepts_feature_and_epic() -> None:
    args = build_parser().parse_args(["run", "specs/001-demo", "EPIC-002"])

    assert args.feature == "specs/001-demo"
    assert args.epic == "EPIC-002"


def test_run_accepts_validation_retries_override() -> None:
    args = build_parser().parse_args(["run", "specs/001-demo", "--validation-retries", "7"])

    assert args.validation_retries == 7


def test_run_accepts_validation_timeout_override() -> None:
    args = build_parser().parse_args(["run", "specs/001-demo", "--validation-timeout-ms", "120000"])

    assert args.validation_timeout_ms == 120000


def test_resume_accepts_validation_retries_override() -> None:
    args = build_parser().parse_args(["resume", "specs/001-demo", "--validation-retries", "7"])

    assert args.validation_retries == 7


def test_version_and_update_switches_parse() -> None:
    parser = build_parser()

    assert parser.parse_args(["--version"]).version is True
    assert parser.parse_args(["--update"]).update is True


def test_declared_versions_match() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))["project"]

    assert speckit_orchestra.__version__ == project["version"]


def test_migrate_command_parse() -> None:
    args = build_parser().parse_args(["migrate", "--dry-run", "--no-backup"])

    assert args.command == "migrate"
    assert args.dry_run is True
    assert args.no_backup is True


def test_clean_command_parse() -> None:
    args = build_parser().parse_args(["clean", "--runtime-only", "--dry-run", "--yes"])

    assert args.command == "clean"
    assert args.runtime_only is True
    assert args.dry_run is True
    assert args.yes is True


def test_doctor_accepts_config_dir() -> None:
    args = build_parser().parse_args(["doctor", "--config-dir", ".custom-orchestra"])

    assert args.command == "doctor"
    assert args.config_dir == ".custom-orchestra"


def test_project_version_doctor_warns_for_stale_metadata(tmp_path: Path) -> None:
    config = default_config(tmp_path)
    config.tool.versionMigrated = "0.1.0"
    config.tool.lastMigratedAt = "2026-01-01T00:00:00Z"
    write_config(tmp_path, config)

    checks = _version_doctor_checks(tmp_path, load_config(tmp_path), ".spec-orchestra", include_path=False)

    metadata = next(check for check in checks if check["name"] == "Project CLI metadata")
    assert metadata["level"] == "warn"
    assert "run `sko migrate`" in str(metadata["detail"])


def test_path_version_doctor_warns_for_version_mismatch(monkeypatch: pytest.MonkeyPatch) -> None:
    import speckit_orchestra.cli as cli

    monkeypatch.setattr(cli.shutil, "which", lambda command: "/usr/local/bin/sko" if command == "sko" else None)
    monkeypatch.setattr(cli, "_version_from_executable", lambda path: "0.1.0")

    checks = _path_version_checks(speckit_orchestra.__version__)

    assert checks[0]["level"] == "warn"
    assert f"current CLI is {speckit_orchestra.__version__}" in str(checks[0]["detail"])


def test_version_comparison_uses_semver_order() -> None:
    assert _is_newer_version("0.10.0", "0.2.0") is True
    assert _is_newer_version("0.2.0", "0.10.0") is False


def test_discover_feature_paths_uses_spec_root(tmp_path: Path) -> None:
    feature = make_feature(tmp_path)
    (tmp_path / "specs" / "not-a-feature").mkdir()

    assert discover_feature_paths(tmp_path) == [feature.resolve()]


def test_missing_feature_requires_interactive_terminal(tmp_path: Path) -> None:
    make_feature(tmp_path)
    config = default_config(tmp_path)

    with pytest.raises(ValueError, match="feature argument is required"):
        _resolve_feature_arg(Namespace(feature=None), tmp_path, config)


def test_explicit_feature_does_not_require_interactive_terminal(tmp_path: Path) -> None:
    config = default_config(tmp_path)

    feature = _resolve_feature_arg(Namespace(feature="specs/001-demo"), tmp_path, config)

    assert feature == "specs/001-demo"


def test_feature_id_resolves_under_spec_root(tmp_path: Path) -> None:
    make_feature(tmp_path)
    config = default_config(tmp_path)

    feature = _resolve_feature_arg(Namespace(feature="001-demo"), tmp_path, config)

    assert feature == "specs/001-demo"


def test_menu_lines_show_selector_controls() -> None:
    lines = _menu_lines("Feature", ["specs/001-demo", "specs/002-api"], 1, current="specs/001-demo")

    assert any("> specs/002-api" in line for line in lines)
    assert any("[current]" in line for line in lines)
    assert "j/k" in lines[-1]
