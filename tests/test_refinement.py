from __future__ import annotations

from pathlib import Path

from speckit_orchestra.config import default_config
from speckit_orchestra.refinement import generate_epic_document


def write_feature(root: Path) -> Path:
    feature = root / "specs" / "001-demo"
    feature.mkdir(parents=True)
    (feature / "spec.md").write_text("# Demo\n", encoding="utf-8")
    (feature / "plan.md").write_text("# Plan\n", encoding="utf-8")
    (feature / "tasks.md").write_text(
        """# Tasks

## Data Model
- [ ] T001 Create src/models/user.py
- [ ] T002 Add tests/models/test_user.py

## API
- [ ] T003 Implement src/api/users.py
""",
        encoding="utf-8",
    )
    return feature


def test_generate_epics_preserves_tasks(tmp_path: Path) -> None:
    feature = write_feature(tmp_path)
    config = default_config(tmp_path)
    doc = generate_epic_document(tmp_path, str(feature), config)

    task_ids = [task for epic in doc.epics for task in epic.tasks]
    assert task_ids == ["T001", "T002", "T003"]
    assert doc.epics[1].dependencies == ["EPIC-001"]
    assert doc.feature.id == "001-demo"


def test_generated_scope_excludes_spec_artifacts(tmp_path: Path) -> None:
    feature = write_feature(tmp_path)
    config = default_config(tmp_path)
    doc = generate_epic_document(tmp_path, str(feature), config)

    assert "specs/001-demo/spec.md" in doc.epics[0].scope.exclude
    assert ".spec-orchestra/**" in doc.epics[0].scope.exclude


def test_generate_epics_ignores_non_task_references(tmp_path: Path) -> None:
    feature = write_feature(tmp_path)
    (feature / "tasks.md").write_text(
        """# Tasks

## Implementation
- [ ] T001 Create src/models/user.py
- [ ] T002 Add tests/models/test_user.py

## Dependencies & Execution Order
- Implementation mentions T001 and T002 for ordering context.

## Parallel Example: Implementation
```text
Task: "T001 Create src/models/user.py"
Task: "T002 Add tests/models/test_user.py"
```
""",
        encoding="utf-8",
    )
    config = default_config(tmp_path)

    doc = generate_epic_document(tmp_path, str(feature), config)

    task_ids = [task for epic in doc.epics for task in epic.tasks]
    assert task_ids == ["T001", "T002"]


def test_generated_scope_preserves_root_file_extensions(tmp_path: Path) -> None:
    feature = write_feature(tmp_path)
    (feature / "tasks.md").write_text(
        """# Tasks

## Setup
- [ ] T001 Configure `package.json`, `tsconfig.app.json`, and `index.html`
- [ ] T002 Implement `src/App.tsx`
""",
        encoding="utf-8",
    )
    config = default_config(tmp_path)

    doc = generate_epic_document(tmp_path, str(feature), config)

    assert "package.json" in doc.epics[0].scope.include
    assert "tsconfig.app.json" in doc.epics[0].scope.include
    assert "index.html" in doc.epics[0].scope.include
    assert "package.js" not in doc.epics[0].scope.include
    assert "tsconfig.app.js" not in doc.epics[0].scope.include


def test_frontend_setup_scope_allows_scaffold_outputs(tmp_path: Path) -> None:
    feature = write_feature(tmp_path)
    (feature / "plan.md").write_text("# Plan\nUse Vite, React, TypeScript, Vitest, and Playwright.\n", encoding="utf-8")
    (feature / "tasks.md").write_text(
        """# Tasks

## Phase 1: Setup
- [ ] T001 Initialize Vite React TypeScript project dependencies and scripts in `package.json` and root HTML shell in `index.html`
""",
        encoding="utf-8",
    )
    config = default_config(tmp_path)

    doc = generate_epic_document(tmp_path, str(feature), config)

    assert ".gitignore" in doc.epics[0].scope.include
    assert "package-lock.json" in doc.epics[0].scope.include
    assert "src/**" in doc.epics[0].scope.include
    assert doc.epics[0].validation.commands == ["npm run typecheck"]


def test_frontend_test_first_epics_are_manual_expected_failure(tmp_path: Path) -> None:
    feature = write_feature(tmp_path)
    (feature / "plan.md").write_text("# Plan\nUse Vite, React, TypeScript, Vitest, and Playwright.\n", encoding="utf-8")
    (feature / "tasks.md").write_text(
        """# Tasks

## Tests for User Story 1
Write these tests first and confirm they fail before implementation.

- [ ] T001 Add unit tests in `tests/unit/todo-state.test.ts`
""",
        encoding="utf-8",
    )
    config = default_config(tmp_path)

    doc = generate_epic_document(tmp_path, str(feature), config)

    assert doc.epics[0].validation.commands == []
    assert doc.epics[0].validation.manualChecks
    assert doc.epics[0].validation.expectedFailureAllowed is True


def test_explicit_spec_doc_task_is_allowed_in_scope(tmp_path: Path) -> None:
    feature = write_feature(tmp_path)
    (feature / "quickstart.md").write_text("# Quickstart\n", encoding="utf-8")
    (feature / "tasks.md").write_text(
        """# Tasks

## Polish
- [ ] T001 Update implementation notes in `specs/001-demo/quickstart.md`
""",
        encoding="utf-8",
    )
    config = default_config(tmp_path)

    doc = generate_epic_document(tmp_path, str(feature), config)

    assert "specs/001-demo/quickstart.md" in doc.epics[0].scope.include
    assert "specs/001-demo/quickstart.md" not in doc.epics[0].scope.exclude


def test_frontend_polish_uses_quickstart_commands(tmp_path: Path) -> None:
    feature = write_feature(tmp_path)
    (feature / "plan.md").write_text("# Plan\nUse Vite and React.\n", encoding="utf-8")
    (feature / "quickstart.md").write_text(
        """# Quickstart

## Development

```bash
npm run dev
```

## Expected Verification Commands

```bash
npm run typecheck
npm run lint
npm run test:unit
npm run build
```
""",
        encoding="utf-8",
    )
    (feature / "tasks.md").write_text(
        """# Tasks

## Phase 7: Polish & Cross-Cutting Concerns
- [ ] T001 Run `npm run typecheck`, `npm run lint`, `npm run test:unit`, and `npm run build`
""",
        encoding="utf-8",
    )
    config = default_config(tmp_path)

    doc = generate_epic_document(tmp_path, str(feature), config)

    assert doc.epics[0].validation.commands == [
        "npm run typecheck",
        "npm run lint",
        "npm run test:unit",
        "npm run build",
    ]
