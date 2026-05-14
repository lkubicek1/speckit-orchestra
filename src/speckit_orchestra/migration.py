from __future__ import annotations

import copy
import json
import shutil
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from .config import Config, config_path, default_config
from .state import summarize
from .utils import atomic_write_json, read_yaml, write_yaml


CURRENT_CONFIG_VERSION = 1
CURRENT_STATE_VERSION = 1


@dataclass
class MigratedFile:
    path: Path
    changed: bool
    details: list[str] = field(default_factory=list)
    backup: Path | None = None


@dataclass
class MigrationResult:
    files: list[MigratedFile] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    dry_run: bool = False

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def changed(self) -> bool:
        return any(item.changed for item in self.files)


def migrate_project(
    root: Path,
    *,
    config_dir: str = ".spec-orchestra",
    dry_run: bool = False,
    backup: bool = True,
) -> MigrationResult:
    """Migrate project-local speckit-orchestra artifacts to the current schema."""
    result = MigrationResult(dry_run=dry_run)
    config_file = config_path(root, config_dir)
    if not config_file.exists():
        result.errors.append(f"project is not initialized; missing {config_file}")
        return result

    backup_root = root / config_dir / "migrations" / _migration_id()
    orchestra_root = root / config_dir
    _migrate_config(
        root,
        config_file,
        config_dir,
        result,
        dry_run=dry_run,
        backup=backup,
        backup_root=backup_root,
        backup_anchor=orchestra_root,
    )
    if result.errors:
        return result

    features_root = root / config_dir / "features"
    if features_root.exists():
        for state_file in sorted(features_root.glob("*/state.json")):
            _migrate_state(
                state_file,
                result,
                dry_run=dry_run,
                backup=backup,
                backup_root=backup_root,
                backup_anchor=orchestra_root,
            )
    return result


def _migrate_config(
    root: Path,
    path: Path,
    config_dir: str,
    result: MigrationResult,
    *,
    dry_run: bool,
    backup: bool,
    backup_root: Path,
    backup_anchor: Path,
) -> None:
    try:
        raw = read_yaml(path)
    except Exception as exc:
        result.errors.append(f"failed to read {path}: {exc}")
        return
    if not isinstance(raw, dict):
        result.errors.append(f"{path} must contain a YAML mapping")
        return

    version = _int_version(raw.get("version", 0))
    if version is None:
        result.errors.append(f"{path} has a non-integer version")
        return
    if version > CURRENT_CONFIG_VERSION:
        result.errors.append(
            f"{path} uses config version {version}, which is newer than supported version {CURRENT_CONFIG_VERSION}"
        )
        return

    normalized, details, warnings = _normalize_config(root, raw, config_dir)
    result.warnings.extend(f"{path}: {warning}" for warning in warnings)
    try:
        config = Config.model_validate(normalized)
    except ValidationError as exc:
        result.errors.append(f"{path} could not be migrated to the current schema: {exc}")
        return

    migrated = config.model_dump(mode="json")
    changed = migrated != raw
    file_result = MigratedFile(path=path, changed=changed, details=details)
    if changed and not dry_run:
        if backup:
            file_result.backup = _backup_file(path, backup_root, backup_anchor)
        write_yaml(path, migrated)
    result.files.append(file_result)


def _normalize_config(root: Path, raw: dict[str, Any], config_dir: str) -> tuple[dict[str, Any], list[str], list[str]]:
    defaults = default_config(root, config_dir=config_dir).model_dump(mode="json")
    normalized = copy.deepcopy(defaults)
    details: list[str] = []
    warnings: list[str] = []

    for key, value in raw.items():
        if key not in defaults:
            warnings.append(f"dropping unsupported top-level key {key!r}")
            details.append(f"drop unsupported key {key}")
            continue
        if isinstance(defaults[key], dict):
            if value is None:
                details.append(f"restore default section {key}")
                continue
            if not isinstance(value, dict):
                normalized[key] = value
                continue
            for child_key, child_value in value.items():
                if child_key not in defaults[key]:
                    warnings.append(f"dropping unsupported key {key}.{child_key}")
                    details.append(f"drop unsupported key {key}.{child_key}")
                    continue
                normalized[key][child_key] = child_value
            for child_key in defaults[key]:
                if child_key not in value:
                    details.append(f"add default {key}.{child_key}")
            continue
        normalized[key] = value

    if raw.get("version") != CURRENT_CONFIG_VERSION:
        details.append(f"set config version {CURRENT_CONFIG_VERSION}")
    normalized["version"] = CURRENT_CONFIG_VERSION

    for key, value in defaults.items():
        if key == "version" or key in raw:
            continue
        if isinstance(value, dict):
            details.append(f"add default {key} section")
        else:
            details.append(f"add default {key}")
    if normalized["project"].get("orchestraRoot") != config_dir:
        warnings.append(
            "project.orchestraRoot does not match --config-dir; migrating the selected config file in place"
        )
    return normalized, details, warnings


def _migrate_state(
    path: Path,
    result: MigrationResult,
    *,
    dry_run: bool,
    backup: bool,
    backup_root: Path,
    backup_anchor: Path,
) -> None:
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        result.warnings.append(f"{path}: skipping unreadable state file: {exc}")
        return
    if not isinstance(state, dict):
        result.warnings.append(f"{path}: skipping state file that does not contain a JSON object")
        return

    version = _int_version(state.get("version", 0))
    if version is None:
        result.warnings.append(f"{path}: skipping state file with non-integer version")
        return
    if version > CURRENT_STATE_VERSION:
        result.warnings.append(
            f"{path}: skipping state version {version}, which is newer than supported version {CURRENT_STATE_VERSION}"
        )
        return

    migrated = copy.deepcopy(state)
    details: list[str] = []
    if migrated.get("version") != CURRENT_STATE_VERSION:
        migrated["version"] = CURRENT_STATE_VERSION
        details.append(f"set state version {CURRENT_STATE_VERSION}")
    if not isinstance(migrated.get("epics"), dict):
        migrated["epics"] = {}
        details.append("add empty epics state")
    summary = summarize(migrated)
    if migrated.get("summary") != summary:
        migrated["summary"] = summary
        details.append("refresh state summary")

    changed = migrated != state
    file_result = MigratedFile(path=path, changed=changed, details=details)
    if changed and not dry_run:
        if backup:
            file_result.backup = _backup_file(path, backup_root, backup_anchor)
        atomic_write_json(path, migrated)
    result.files.append(file_result)


def _backup_file(path: Path, backup_root: Path, anchor: Path) -> Path:
    try:
        relative = path.resolve().relative_to(anchor.resolve())
    except ValueError:
        relative = Path(path.name)
    target = backup_root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, target)
    return target


def _int_version(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _migration_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
