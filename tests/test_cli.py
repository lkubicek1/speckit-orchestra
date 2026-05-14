from __future__ import annotations

from argparse import Namespace
from pathlib import Path

import pytest

from speckit_orchestra.cli import _menu_lines, _resolve_feature_arg, build_parser
from speckit_orchestra.config import default_config
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


def test_version_and_update_switches_parse() -> None:
    parser = build_parser()

    assert parser.parse_args(["--version"]).version is True
    assert parser.parse_args(["--update"]).update is True


def test_migrate_command_parse() -> None:
    args = build_parser().parse_args(["migrate", "--dry-run", "--no-backup"])

    assert args.command == "migrate"
    assert args.dry_run is True
    assert args.no_backup is True


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


def test_menu_lines_show_selector_controls() -> None:
    lines = _menu_lines("Feature", ["specs/001-demo", "specs/002-api"], 1, current="specs/001-demo")

    assert any("> specs/002-api" in line for line in lines)
    assert any("[current]" in line for line in lines)
    assert "j/k" in lines[-1]
