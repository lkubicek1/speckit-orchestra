from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path

from .config import Config
from .epics import EpicDocument
from .feature import Task, artifact_relpaths, load_feature_artifacts, missing_required_artifacts, parse_tasks
from .utils import relpath


PATH_RE = re.compile(
    r"(?:(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_./*{}-]+|[A-Za-z0-9_.-]+\.(?:py|ts|tsx|js|jsx|json|ya?ml|md|toml|go|rs|java|kt|cs|rb|php|sql|sh))"
)
RISK_WORDS = {"auth", "security", "migration", "database", "payment", "billing", "secret", "infra", "deploy"}


def generate_epic_document(root: Path, feature: str, config: Config, *, agent: str | None = None) -> EpicDocument:
    artifacts = load_feature_artifacts(root, feature)
    missing = missing_required_artifacts(artifacts)
    if missing:
        missing_text = ", ".join(relpath(path, root) for path in missing)
        raise ValueError(f"missing required Spec Kit artifacts: {missing_text}")

    tasks = parse_tasks(artifacts.tasks.read_text(encoding="utf-8"))
    if not tasks:
        raise ValueError(f"no Spec Kit task IDs like T001 were found in {relpath(artifacts.tasks, root)}")

    grouped = _group_tasks(tasks)
    epics: list[dict[str, object]] = []
    previous_id: str | None = None
    index = 1
    for title, group in grouped:
        for chunk in _chunks(group, 6):
            epic_id = f"EPIC-{index:03d}"
            risk = _risk_for(title, chunk)
            approval_required = risk == "high"
            epics.append(
                {
                    "id": epic_id,
                    "title": _epic_title(title, index, len(grouped)),
                    "goal": _goal_for(title, chunk),
                    "tasks": [task.id for task in chunk],
                    "dependencies": [previous_id] if previous_id else [],
                    "risk": risk,
                    "parallelSafe": False,
                    "approval": {
                        "required": approval_required,
                        "reason": "Potentially sensitive or persistent-system work." if approval_required else None,
                    },
                    "scope": {
                        "include": _scope_include(chunk),
                        "exclude": _scope_exclude(root, artifacts),
                    },
                    "acceptance": _acceptance_for(chunk),
                    "validation": _validation_for(root),
                    "stopConditions": [
                        "Required context is missing from spec.md, plan.md, or tasks.md.",
                        "The implementation requires edits outside the declared scope.",
                        "Validation cannot pass without a requirement change or missing secret.",
                    ],
                }
            )
            previous_id = epic_id
            index += 1

    feature_ref = {
        "id": artifacts.id,
        "path": relpath(artifacts.path, root),
        "spec": relpath(artifacts.spec, root),
        "plan": relpath(artifacts.plan, root),
        "tasks": relpath(artifacts.tasks, root),
    }
    return EpicDocument.model_validate(
        {
            "version": 1,
            "feature": feature_ref,
            "execution": {"recommendedMode": "sequential", "recommendedAdapter": agent or config.agent.adapter},
            "epics": epics,
            "excludedTasks": [],
        }
    )


def _group_tasks(tasks: list[Task]) -> list[tuple[str, list[Task]]]:
    groups: OrderedDict[str, list[Task]] = OrderedDict()
    for task in tasks:
        section = task.section if task.section.lower() not in {"tasks", "format"} else "Implementation"
        groups.setdefault(section, []).append(task)
    return list(groups.items())


def _chunks(items: list[Task], size: int) -> list[list[Task]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def _epic_title(section: str, index: int, group_count: int) -> str:
    if group_count == 1 and section == "Implementation":
        return f"Implementation slice {index}"
    return section[:80]


def _goal_for(section: str, tasks: list[Task]) -> str:
    ids = ", ".join(task.id for task in tasks)
    return f"Complete the {section.lower()} work represented by {ids}."


def _risk_for(title: str, tasks: list[Task]) -> str:
    text = " ".join([title, *(task.text for task in tasks)]).lower()
    if any(word in text for word in RISK_WORDS):
        return "high"
    if len(tasks) > 4:
        return "medium"
    return "low"


def _scope_include(tasks: list[Task]) -> list[str]:
    globs: set[str] = set()
    for task in tasks:
        for match in PATH_RE.findall(task.text):
            path = match.strip("`.,;:()[]")
            if not path or path.startswith(("http://", "https://")):
                continue
            if "/" in path:
                first = path.split("/", 1)[0]
                if first not in {"specs", ".spec-orchestra"}:
                    globs.add(f"{first}/**")
            else:
                globs.add(path)
    return sorted(globs) or ["**/*"]


def _scope_exclude(root: Path, artifacts) -> list[str]:
    excluded = {".git/**", ".spec-orchestra/**"}
    excluded.update(artifact_relpaths(root, artifacts)[:3])
    return sorted(excluded)


def _acceptance_for(tasks: list[Task]) -> list[str]:
    return [
        "All in-scope task IDs are implemented: " + ", ".join(task.id for task in tasks) + ".",
        "Changes stay within the declared scope and do not modify Spec Kit source artifacts.",
        "Configured validation commands pass, or listed manual checks are completed.",
    ]


def _validation_for(root: Path) -> dict[str, list[str]]:
    if (root / "package.json").exists():
        return {"commands": ["npm test"], "manualChecks": []}
    if (root / "pyproject.toml").exists():
        return {"commands": ["uv run pytest"], "manualChecks": []}
    return {"commands": [], "manualChecks": ["Review the changed files against the epic acceptance criteria."]}
