from __future__ import annotations

import json
from pathlib import Path

import yaml

from speckit_orchestra.config import default_config, load_config, write_config
from speckit_orchestra.migration import migrate_project


def write_yaml_file(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def test_migrate_normalizes_config_and_backs_up_original(tmp_path: Path) -> None:
    config_file = tmp_path / ".spec-orchestra" / "config.yaml"
    write_yaml_file(
        config_file,
        {
            "version": 0,
            "project": {"name": "demo"},
            "agent": {"adapter": "opencode", "command": "opencode", "args": ["run"], "legacy": True},
            "legacyTop": "remove-me",
        },
    )

    result = migrate_project(tmp_path)

    assert result.ok
    assert result.changed
    assert any("legacyTop" in warning for warning in result.warnings)
    assert any("agent.legacy" in warning for warning in result.warnings)
    config = load_config(tmp_path)
    assert config.version == 1
    assert config.project.name == "demo"
    assert config.agent.mode == "cli"
    assert config.agent.timeoutMs == 1_800_000
    migrated = yaml.safe_load(config_file.read_text(encoding="utf-8"))
    assert "legacyTop" not in migrated
    assert "legacy" not in migrated["agent"]
    backups = [item.backup for item in result.files if item.path == config_file]
    assert backups and backups[0] is not None and backups[0].exists()
    original = yaml.safe_load(backups[0].read_text(encoding="utf-8"))
    assert original["agent"]["legacy"] is True


def test_migrate_dry_run_does_not_write_or_backup(tmp_path: Path) -> None:
    config_file = tmp_path / ".spec-orchestra" / "config.yaml"
    original = {"version": 0, "project": {"name": "demo"}}
    write_yaml_file(config_file, original)

    result = migrate_project(tmp_path, dry_run=True)

    assert result.ok
    assert result.changed
    assert yaml.safe_load(config_file.read_text(encoding="utf-8")) == original
    assert not (tmp_path / ".spec-orchestra" / "migrations").exists()


def test_migrate_refreshes_runtime_state_summary(tmp_path: Path) -> None:
    write_config(tmp_path, default_config(tmp_path))
    state_file = tmp_path / ".spec-orchestra" / "features" / "001-demo" / "state.json"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        json.dumps(
            {
                "version": 0,
                "status": "running",
                "epics": {
                    "EPIC-001": {"status": "complete"},
                    "EPIC-002": {"status": "blocked"},
                },
                "summary": {"total": 99, "complete": 0, "blocked": 0, "pending": 99, "failed": 0},
            }
        ),
        encoding="utf-8",
    )

    result = migrate_project(tmp_path, backup=False)

    assert result.ok
    migrated = json.loads(state_file.read_text(encoding="utf-8"))
    assert migrated["version"] == 1
    assert migrated["summary"] == {"total": 2, "complete": 1, "blocked": 1, "pending": 0, "failed": 0}


def test_migrate_reports_missing_config(tmp_path: Path) -> None:
    result = migrate_project(tmp_path)

    assert not result.ok
    assert any("not initialized" in error for error in result.errors)
