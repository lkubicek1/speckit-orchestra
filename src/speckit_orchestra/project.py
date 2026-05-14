from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path


EXCLUDE_BEGIN = "# BEGIN speckit-orchestra"
EXCLUDE_END = "# END speckit-orchestra"


@dataclass
class CleanResult:
    removed: list[Path] = field(default_factory=list)
    would_remove: list[Path] = field(default_factory=list)
    updated_exclude: bool = False
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def ensure_git_info_exclude(root: Path, orchestra_root: str) -> bool:
    """Install local git excludes for generated run artifacts.

    The excludes live in .git/info/exclude so project files are not modified.
    Config and epics remain reviewable; only volatile runtime output is ignored.
    """

    exclude_path = _git_info_exclude_path(root)
    if exclude_path is None:
        return False
    block = _exclude_block(orchestra_root)
    current = exclude_path.read_text(encoding="utf-8") if exclude_path.exists() else ""
    updated = _replace_managed_block(current, block)
    if updated == current:
        return False
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text(updated, encoding="utf-8")
    return True


def remove_git_info_exclude(root: Path) -> bool:
    exclude_path = _git_info_exclude_path(root)
    if exclude_path is None or not exclude_path.exists():
        return False
    current = exclude_path.read_text(encoding="utf-8")
    updated = _remove_managed_block(current)
    if updated == current:
        return False
    exclude_path.write_text(updated, encoding="utf-8")
    return True


def clean_project(root: Path, *, config_dir: str = ".spec-orchestra", dry_run: bool = False, runtime_only: bool = False) -> CleanResult:
    result = CleanResult()
    target = (root / config_dir).resolve()
    if not _safe_target(root, target):
        result.errors.append(f"refusing to remove unsafe path: {target}")
        return result

    paths = _runtime_paths(target) if runtime_only else ([target] if target.exists() else [])
    for path in paths:
        if not path.exists():
            continue
        if dry_run:
            result.would_remove.append(path)
            continue
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        result.removed.append(path)

    if not runtime_only and not dry_run:
        result.updated_exclude = remove_git_info_exclude(root)
    return result


def _exclude_block(orchestra_root: str) -> str:
    root = orchestra_root.strip("/") or ".spec-orchestra"
    patterns = [
        f"/{root}/features/*/state.json",
        f"/{root}/features/*/events.jsonl",
        f"/{root}/features/*/lock.json",
        f"/{root}/features/*/runs/",
        f"/{root}/features/*/reports/",
        f"/{root}/migrations/",
    ]
    return "\n".join([EXCLUDE_BEGIN, *patterns, EXCLUDE_END, ""])


def _git_info_exclude_path(root: Path) -> Path | None:
    git_dir = root / ".git"
    if not git_dir.is_dir():
        return None
    return git_dir / "info" / "exclude"


def _replace_managed_block(text: str, block: str) -> str:
    stripped = _remove_managed_block(text).rstrip()
    if stripped:
        return f"{stripped}\n\n{block}"
    return block


def _remove_managed_block(text: str) -> str:
    lines = text.splitlines()
    output: list[str] = []
    skipping = False
    for line in lines:
        if line.strip() == EXCLUDE_BEGIN:
            skipping = True
            continue
        if skipping and line.strip() == EXCLUDE_END:
            skipping = False
            continue
        if not skipping:
            output.append(line)
    suffix = "\n" if text.endswith("\n") and output else ""
    return "\n".join(output).rstrip() + suffix


def _runtime_paths(target: Path) -> list[Path]:
    paths: list[Path] = []
    features = target / "features"
    if features.exists():
        for feature_dir in sorted(path for path in features.iterdir() if path.is_dir()):
            paths.extend(
                [
                    feature_dir / "state.json",
                    feature_dir / "events.jsonl",
                    feature_dir / "lock.json",
                    feature_dir / "runs",
                    feature_dir / "reports",
                ]
            )
    paths.append(target / "migrations")
    return paths


def _safe_target(root: Path, target: Path) -> bool:
    root = root.resolve()
    try:
        relative = target.relative_to(root)
    except ValueError:
        return False
    return bool(relative.parts) and relative.parts not in {(), (".",)}
