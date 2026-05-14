from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .utils import relpath


TASK_RE = re.compile(r"\b(T\d{3,})\b")
HEADING_RE = re.compile(r"^\s{0,3}#{2,5}\s+(.+?)\s*$")
CHECKBOX_RE = re.compile(r"^\s*[-*]\s+\[[ xX]\]\s*")
REQUIRED_ARTIFACT_NAMES = ("spec.md", "plan.md", "tasks.md")


@dataclass(frozen=True)
class Task:
    id: str
    text: str
    line: int
    section: str


@dataclass(frozen=True)
class FeatureArtifacts:
    id: str
    path: Path
    spec: Path
    plan: Path
    tasks: Path
    optional: tuple[Path, ...]

    def source_paths(self) -> list[Path]:
        return [self.spec, self.plan, self.tasks, *self.optional]


def resolve_feature_path(root: Path, feature: str) -> Path:
    path = Path(feature)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def load_feature_artifacts(root: Path, feature: str) -> FeatureArtifacts:
    path = resolve_feature_path(root, feature)
    optional: list[Path] = []
    for name in ("research.md", "data-model.md", "quickstart.md"):
        candidate = path / name
        if candidate.exists():
            optional.append(candidate)
    contracts = path / "contracts"
    if contracts.exists():
        optional.extend(sorted(p for p in contracts.rglob("*") if p.is_file()))
    return FeatureArtifacts(
        id=path.name,
        path=path,
        spec=path / "spec.md",
        plan=path / "plan.md",
        tasks=path / "tasks.md",
        optional=tuple(optional),
    )


def discover_feature_paths(root: Path, spec_root: str = "specs") -> list[Path]:
    path = Path(spec_root)
    if not path.is_absolute():
        path = root / path
    if not path.exists():
        return []

    features: list[Path] = []
    for candidate in path.iterdir():
        if not candidate.is_dir() or candidate.name.startswith("."):
            continue
        if any((candidate / name).exists() for name in REQUIRED_ARTIFACT_NAMES):
            features.append(candidate.resolve())
    return sorted(features, key=lambda feature: feature.name)


def missing_required_artifacts(artifacts: FeatureArtifacts) -> list[Path]:
    return [path for path in (artifacts.spec, artifacts.plan, artifacts.tasks) if not path.exists()]


def parse_tasks(tasks_md: str) -> list[Task]:
    tasks: list[Task] = []
    section = "Implementation"
    for line_no, line in enumerate(tasks_md.splitlines(), start=1):
        heading = HEADING_RE.match(line)
        if heading:
            section = _clean_heading(heading.group(1))
            continue
        match = TASK_RE.search(line)
        if not match:
            continue
        task_id = match.group(1)
        text = CHECKBOX_RE.sub("", line).strip()
        text = re.sub(r"\s*\[[^\]]+\]\s*", " ", text)
        text = re.sub(r"\bT\d{3,}\b", "", text, count=1).strip(" -:")
        tasks.append(Task(id=task_id, text=text or line.strip(), line=line_no, section=section))
    return tasks


def artifact_relpaths(root: Path, artifacts: FeatureArtifacts) -> list[str]:
    return [relpath(path, root) for path in artifacts.source_paths()]


def _clean_heading(value: str) -> str:
    value = re.sub(r"^[0-9.]+\s*", "", value)
    value = value.strip(" #")
    return value or "Implementation"
