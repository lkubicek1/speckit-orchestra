from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .adapters import get_adapter
from .config import Config
from .epics import EpicDocument, load_epics
from .feature import load_feature_artifacts, missing_required_artifacts, parse_tasks
from .git import is_repo
from .utils import relpath


@dataclass
class ValidationReport:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    epics: EpicDocument | None = None

    @property
    def ok(self) -> bool:
        return not self.errors


def epics_path(root: Path, config: Config, feature_id: str) -> Path:
    return root / config.project.orchestraRoot / "features" / feature_id / "epics.yaml"


def feature_state_dir(root: Path, config: Config, feature_id: str) -> Path:
    return root / config.project.orchestraRoot / "features" / feature_id


def validate_feature(root: Path, feature: str, config: Config, *, check_git: bool = True) -> ValidationReport:
    report = ValidationReport()
    artifacts = load_feature_artifacts(root, feature)
    for missing in missing_required_artifacts(artifacts):
        report.errors.append(f"missing required artifact: {relpath(missing, root)}")
    if report.errors:
        return report

    source_tasks = parse_tasks(artifacts.tasks.read_text(encoding="utf-8"))
    source_ids = [task.id for task in source_tasks]
    if not source_ids:
        report.errors.append(f"no task IDs like T001 found in {relpath(artifacts.tasks, root)}")
    duplicates = [task_id for task_id, count in Counter(source_ids).items() if count > 1]
    if duplicates:
        report.errors.append("duplicate task IDs in tasks.md: " + ", ".join(sorted(duplicates)))

    path = epics_path(root, config, artifacts.id)
    if not path.exists():
        report.errors.append(f"epics.yaml does not exist: {relpath(path, root)}")
        return report

    try:
        doc = load_epics(path)
    except ValidationError as exc:
        report.errors.append(f"epics.yaml schema validation failed: {exc}")
        return report
    except Exception as exc:
        report.errors.append(str(exc))
        return report
    report.epics = doc

    if doc.feature.id != artifacts.id:
        report.errors.append(f"epics feature id {doc.feature.id!r} does not match directory {artifacts.id!r}")
    if doc.feature.tasks != relpath(artifacts.tasks, root):
        report.warnings.append("epics feature.tasks does not match the current tasks.md path")

    excluded = {item.id for item in doc.excludedTasks}
    epic_task_counts: Counter[str] = Counter()
    for epic in doc.epics:
        epic_task_counts.update(epic.tasks)
        if not epic.scope.include:
            report.errors.append(f"{epic.id} has no scope.include patterns")
        if not epic.validation.commands and not epic.validation.manualChecks:
            report.errors.append(f"{epic.id} has no validation commands or manual checks")
        if epic.approval.required:
            report.warnings.append(f"{epic.id} requires interactive approval before run: {epic.approval.reason or 'approval required'}")
        if epic.validation.expectedFailureAllowed and epic.validation.commands:
            report.warnings.append(f"{epic.id} allows failing validation commands for test-first work")

    source_set = set(source_ids)
    used = set(epic_task_counts)
    missing = sorted(source_set - used - excluded)
    unknown = sorted(used - source_set)
    duplicated = sorted(task_id for task_id, count in epic_task_counts.items() if count > 1)
    if missing:
        report.errors.append("tasks missing from epics.yaml: " + ", ".join(missing))
    if unknown:
        report.errors.append("epics reference unknown task IDs: " + ", ".join(unknown))
    if duplicated:
        report.errors.append("tasks assigned to multiple epics: " + ", ".join(duplicated))

    _validate_dependencies(doc, report)

    if get_adapter(config.agent.adapter) is None:
        report.errors.append(f"configured adapter is not available: {config.agent.adapter}")
    if check_git and not is_repo(root):
        report.errors.append("current directory is not inside a git repository")
    return report


def topological_epics(doc: EpicDocument) -> list[str]:
    ids = {epic.id for epic in doc.epics}
    indegree = {epic.id: 0 for epic in doc.epics}
    children: dict[str, list[str]] = defaultdict(list)
    for epic in doc.epics:
        for dep in epic.dependencies:
            if dep in ids:
                indegree[epic.id] += 1
                children[dep].append(epic.id)
    queue = deque(epic.id for epic in doc.epics if indegree[epic.id] == 0)
    order: list[str] = []
    while queue:
        epic_id = queue.popleft()
        order.append(epic_id)
        for child in children[epic_id]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    if len(order) != len(doc.epics):
        raise ValueError("epic dependency graph contains a cycle")
    return order


def _validate_dependencies(doc: EpicDocument, report: ValidationReport) -> None:
    ids = {epic.id for epic in doc.epics}
    for epic in doc.epics:
        for dep in epic.dependencies:
            if dep not in ids:
                report.errors.append(f"{epic.id} depends on unknown epic {dep}")
    try:
        topological_epics(doc)
    except ValueError as exc:
        report.errors.append(str(exc))
