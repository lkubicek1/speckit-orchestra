from __future__ import annotations

from pathlib import Path

from .epics import Epic
from .feature import FeatureArtifacts, Task, artifact_relpaths
from .utils import relpath


def render_epic_prompt(
    *,
    root: Path,
    artifacts: FeatureArtifacts,
    epic: Epic,
    tasks: list[Task],
    dependency_summary: str,
    validation_failure: str | None = None,
) -> str:
    task_list = "\n".join(f"- {task.id}: {task.text}" for task in tasks if task.id in set(epic.tasks))
    source_artifacts = "\n".join(f"- {path}" for path in artifact_relpaths(root, artifacts))
    prefix = ""
    if validation_failure:
        prefix = f"""The previous attempt for {epic.id} did not pass validation.

## Validation Failure

```text
{validation_failure[-6000:]}
```

## Retry Instructions

Fix only issues required for this epic to pass validation. Do not expand scope. If the failure cannot be fixed without missing information or forbidden changes, stop and report a blocker.

---

"""
    return prefix + f"""You are implementing one epic from a Spec Kit project using speckit-orchestra.

## Epic

ID: {epic.id}
Title: {epic.title}
Goal: {epic.goal}
Risk: {epic.risk}

## Source of Truth

Read these files before making changes:

{source_artifacts}

## Tasks in Scope

{task_list}

## Dependencies Already Completed

{dependency_summary or "- None"}

## Allowed Paths

You may edit files matching:

{_bullets(epic.scope.include)}

## Forbidden Paths

Do not edit files matching:

{_bullets(epic.scope.exclude)}

## Acceptance Criteria

{_bullets(epic.acceptance)}

## Validation Commands

{_bullets(epic.validation.commands) if epic.validation.commands else "- No automated commands. Complete manual checks below."}

## Manual Checks

{_bullets(epic.validation.manualChecks) if epic.validation.manualChecks else "- None"}

## Stop Conditions

Stop and report a blocker if any of these occur:

{_bullets(epic.stopConditions)}

## Spec Kit Implementation Discipline

Follow the relevant parts of the current Spec Kit implementation workflow, adapted to this single-epic run:

- Read the source-of-truth artifacts before editing, including optional design artifacts such as research, data model, contracts, quickstart, and constitution files when present.
- Use `tasks.md` to infer task phase, ordering, dependencies, and test-first expectations for the in-scope task IDs only.
- If tests are in scope, write or update them before the corresponding implementation where practical, and confirm the intended failure before making them pass when feasible.
- Keep project setup and ignore-file updates limited to changes required by this epic and allowed by the path scope.
- Do not run interactive checklist prompts; if an incomplete checklist or hook blocks safe implementation, report it as a blocker.
- Do not mark tasks complete in `tasks.md` unless explicitly instructed to modify Spec Kit source artifacts.
- Validate completion against the epic acceptance criteria and the relevant Spec Kit plan/spec context, not just against the test command exit status.

## Rules

- Implement only this epic.
- Do not begin later epics.
- Do not modify Spec Kit source artifacts unless explicitly asked.
- Prefer minimal, idiomatic changes.
- Add or update tests for changed behavior.
- Do not introduce secrets or credentials.
- If requirements conflict, stop and report a blocker.
- If validation cannot pass, report the exact failure and likely cause.

## Final Response Format

Return:

1. Summary of changes
2. Files changed
3. Tests run
4. Acceptance criteria status
5. Remaining risks
6. Blockers, if any
"""


def dependency_summary_for(epic: Epic, state: dict[str, object]) -> str:
    epics = state.get("epics", {}) if isinstance(state, dict) else {}
    lines: list[str] = []
    for dep in epic.dependencies:
        dep_state = epics.get(dep, {}) if isinstance(epics, dict) else {}
        commit = dep_state.get("commit") if isinstance(dep_state, dict) else None
        suffix = f" ({commit})" if commit else ""
        lines.append(f"- {dep}: complete{suffix}")
    return "\n".join(lines)


def render_attempt_report(
    *,
    epic: Epic,
    attempt: int,
    adapter_status: str,
    exit_code: int | None,
    changed_files: list[str],
    validation_summary: str,
    blocker: dict[str, object] | None,
    validation_heading: str = "Validation",
) -> str:
    return f"""# {epic.id} Attempt {attempt}

- Epic: {epic.title}
- Adapter status: {adapter_status}
- Exit code: {exit_code}

## Changed Files

{_bullets(changed_files) if changed_files else "- None"}

## {validation_heading}

```text
{validation_summary or "No validation was run."}
```

## Blocker

{_blocker_text(blocker)}
"""


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _blocker_text(blocker: dict[str, object] | None) -> str:
    if not blocker:
        return "None"
    lines = [f"- Category: {blocker.get('category', 'unknown')}", f"- Message: {blocker.get('message', '')}"]
    next_action = blocker.get("suggestedNextAction")
    if next_action:
        lines.append(f"- Suggested next action: {next_action}")
    return "\n".join(lines)
