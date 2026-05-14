from __future__ import annotations

import re
from collections import OrderedDict
from pathlib import Path

from .config import Config
from .epics import EpicDocument
from .feature import Task, artifact_relpaths, load_feature_artifacts, missing_required_artifacts, parse_tasks
from .utils import relpath


PATH_RE = re.compile(
    r"(?:(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_./*{}-]+|[A-Za-z0-9_.-]+\.(?:tsx|jsx|json|ya?ml|html|toml|java|php|css|sql|py|ts|js|md|go|rs|kt|cs|rb|sh))"
)
BACKTICK_RE = re.compile(r"`([^`]+)`")
FILE_EXT_RE = re.compile(r"\.(?:tsx|jsx|json|ya?ml|html|toml|java|php|css|sql|py|ts|js|md|go|rs|kt|cs|rb|sh)$")
RISK_WORDS = {"auth", "security", "migration", "database", "payment", "billing", "secret", "infra", "deploy"}
FRONTEND_HINTS = {"vite", "react", "typescript", "npm", "playwright", "vitest", "package.json"}
SETUP_SCOPE_PATTERNS = {".gitignore", "package-lock.json", "eslint.config.*", "public/**", "src/**"}


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
                    "validation": _validation_for(root, artifacts, title, chunk),
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
        for path in _task_paths(task.text):
            pattern = _scope_pattern(path)
            if pattern:
                globs.add(pattern)
    if _looks_like_setup(tasks):
        globs.update(SETUP_SCOPE_PATTERNS)
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


def _validation_for(root: Path, artifacts, section: str, tasks: list[Task]) -> dict[str, object]:
    if _is_frontend_project(root, artifacts, tasks):
        if _is_test_first_epic(section, tasks):
            return {
                "commands": [],
                "manualChecks": ["Confirm tests were added for this story and are expected to fail before implementation."],
                "expectedFailureAllowed": True,
            }
        return {"commands": _frontend_validation_commands(artifacts, section, tasks), "manualChecks": []}
    if (root / "pyproject.toml").exists():
        return {"commands": ["uv run pytest"], "manualChecks": []}
    return {"commands": [], "manualChecks": ["Review the changed files against the epic acceptance criteria."]}


def _task_paths(text: str) -> list[str]:
    matches = BACKTICK_RE.findall(text)
    if not matches:
        matches = PATH_RE.findall(text)
    paths: list[str] = []
    for match in matches:
        for candidate in re.split(r"\s*(?:,|\band\b)\s*", match):
            path = candidate.strip("`.,;:()[] ")
            if path and _looks_like_path(path) and not path.startswith(("http://", "https://")):
                paths.append(path)
    return paths


def _scope_pattern(path: str) -> str | None:
    if path.startswith(".spec-orchestra/") or path == ".spec-orchestra":
        return None
    normalized = path.strip("/")
    if not normalized:
        return None
    if normalized.endswith("/"):
        normalized = normalized.rstrip("/")
    if "/" not in normalized:
        return normalized
    first = normalized.split("/", 1)[0]
    if first == "specs":
        return normalized
    return f"{first}/**"


def _looks_like_path(path: str) -> bool:
    return "/" in path or path.endswith("/") or bool(FILE_EXT_RE.search(path)) or path == ".gitignore"


def _looks_like_setup(tasks: list[Task]) -> bool:
    text = " ".join(task.text for task in tasks).lower()
    return any(marker in text for marker in ("vite", "package.json", "npm", "dependencies", "project dependencies"))


def _is_frontend_project(root: Path, artifacts, tasks: list[Task]) -> bool:
    if (root / "package.json").exists():
        return True
    text = " ".join(
        [
            _read_lower(artifacts.plan),
            _read_lower(artifacts.spec),
            " ".join(task.text for task in tasks).lower(),
        ]
    )
    return any(hint in text for hint in FRONTEND_HINTS)


def _is_test_first_epic(section: str, tasks: list[Task]) -> bool:
    text = " ".join([section, *(task.text for task in tasks)]).lower()
    return section.lower().startswith("tests for") or "fail before implementation" in text or "confirm they fail" in text


def _frontend_validation_commands(artifacts, section: str, tasks: list[Task]) -> list[str]:
    text = " ".join([section, *(task.text for task in tasks)]).lower()
    if "polish" in text or "npm run" in text or "final" in text:
        return _quickstart_commands(artifacts) or [
            "npm run typecheck",
            "npm run lint",
            "npm run test:unit",
            "npm run e2e",
            "npm run build",
        ]
    if "setup" in text:
        return ["npm run typecheck"]
    if "foundational" in text or "blocking prerequisites" in text:
        return ["npm run typecheck", "npm run build"]
    return ["npm run typecheck", "npm run test:unit", "npm run build"]


def _quickstart_commands(artifacts) -> list[str]:
    quickstart = next((path for path in artifacts.optional if path.name == "quickstart.md"), None)
    if quickstart is None:
        return []
    commands: list[str] = []
    text = quickstart.read_text(encoding="utf-8")
    source = _expected_verification_block(text) or text
    for command in re.findall(r"\bnpm run [A-Za-z0-9:_-]+", source):
        if command not in commands:
            commands.append(command)
    return commands


def _expected_verification_block(text: str) -> str:
    lines = text.splitlines()
    in_section = False
    in_fence = False
    collected: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if in_section and collected:
                break
            in_section = "expected verification" in line.lower()
            in_fence = False
            continue
        if not in_section:
            continue
        if line.strip().startswith("```"):
            if in_fence:
                break
            in_fence = True
            continue
        if in_fence:
            collected.append(line)
    return "\n".join(collected)


def _read_lower(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").lower()
    except OSError:
        return ""
