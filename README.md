# speckit-orchestra

`speckit-orchestra` is a CLI-first orchestration layer for Spec Kit projects. It turns a Spec Kit `tasks.md` file into bounded implementation epics, runs each epic through a configured coding-agent CLI, preserves attempt artifacts, validates results, and records durable state for resume/reporting.

The MVP ships with an `opencode` CLI adapter and keeps the orchestration core harness-agnostic.

## Features

- Initialize repository-local orchestration config in `.spec-orchestra/config.yaml`.
- Refine `specs/<feature-id>/tasks.md` into `.spec-orchestra/features/<feature-id>/epics.yaml`.
- Validate Spec Kit artifacts, epic schema, task coverage, dependency graph, scopes, and adapter configuration.
- Execute epics sequentially with one fresh adapter subprocess per attempt.
- Capture prompts, stdout, stderr, exit metadata, changed files, diffs, validation logs, and result reports.
- Enforce forbidden-path and allowed-scope checks.
- Retry validation failures once by default.
- Maintain atomic `state.json`, append-only `events.jsonl`, lock files, and summary reports.
- Support commit modes: `ask`, `auto`, and `never`.
- Migrate initialized project artifacts after CLI upgrades with `sko migrate`.

## Requirements

- Python 3.11 or newer.
- `uv` for installation and local development.
- `git` available on `PATH`.
- `opencode` available on `PATH` when running epics with the default adapter.

Install `uv` if needed:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Installation

From a checkout of this repository:

```bash
uv sync
uv run speckit-orchestra --help
```

Install the CLI into your user environment from the local checkout:

```bash
uv tool install .
speckit-orchestra --help
```

Install directly from a Git URL:

```bash
uv tool install git+https://github.com/lkubicek1/speckit-orchestra.git
```

The package exposes three equivalent commands:

```bash
speckit-orchestra
sko
orchestra
```

## Quick Start

Start from a repository that already has Spec Kit artifacts:

```text
specs/001-user-auth/
  spec.md
  plan.md
  tasks.md
```

Initialize orchestration config:

```bash
speckit-orchestra init --agent opencode --commit-mode ask
```

When run in an interactive terminal, `init` can discover local opencode providers, models, and agents and present a small setup menu. In scripts, pass values directly:

```bash
speckit-orchestra init \
  --agent opencode \
  --provider openai \
  --model gpt-5.5 \
  --variant high \
  --opencode-agent build \
  --commit-mode ask \
  --yes
```

Generate epics from `tasks.md`:

```bash
speckit-orchestra refine specs/001-user-auth
# or omit the feature and pick from the detected-feature menu
speckit-orchestra refine
```

Review and edit the generated file before execution:

```text
.spec-orchestra/features/001-user-auth/epics.yaml
```

Validate readiness:

```bash
speckit-orchestra validate specs/001-user-auth
speckit-orchestra validate
```

Run the full feature:

```bash
speckit-orchestra run specs/001-user-auth
speckit-orchestra run
```

Run one epic only:

```bash
speckit-orchestra run specs/001-user-auth EPIC-002
```

Resume after resolving a blocker:

```bash
speckit-orchestra resume specs/001-user-auth
speckit-orchestra resume
```

Inspect status and reports:

```bash
speckit-orchestra status specs/001-user-auth
speckit-orchestra report specs/001-user-auth
```

Version and update helpers are available from any installed script name:

```bash
sko --version
sko --update
sko migrate --dry-run
sko migrate
```

## Commands

### `init`

Creates `.spec-orchestra/config.yaml`.

```bash
speckit-orchestra init --agent opencode --mode cli --commit-mode ask
```

Useful options:

- `--config-dir <path>` changes the orchestration directory.
- `--commit-mode auto|ask|never` controls commits after successful epics.
- `--provider <id>` sets the provider portion of the opencode model.
- `--model <id>` sets the model, either as `provider/model` or a model name paired with `--provider`.
- `--variant <name>` sets provider-specific reasoning effort, such as `minimal`, `high`, or `max`.
- `--opencode-agent <name>` passes `--agent <name>` to `opencode run`.
- `--thinking` passes `--thinking` to `opencode run`.
- `--discover` or `--no-discover` controls the interactive opencode setup menu.
- `--yes` overwrites an existing config promptlessly.

### `configure`

Updates adapter runtime settings after initialization.

```bash
speckit-orchestra configure
speckit-orchestra configure --model github-copilot/claude-sonnet-4.5 --variant high
speckit-orchestra configure --discover
```

With no direct options in an interactive terminal, `configure` opens the same opencode discovery menu used by `init`. It discovers options with local opencode commands:

- `opencode models`
- `opencode providers list`
- `opencode agent list`

Discovery is best effort. Provider credentials and auth stay in opencode; `speckit-orchestra` only stores project-level runtime selections.

### `refine`

Generates epics from Spec Kit artifacts.

```bash
speckit-orchestra refine specs/001-user-auth --force
speckit-orchestra refine specs/001-user-auth --dry-run
speckit-orchestra refine --dry-run
```

If `feature` is omitted in an interactive terminal, `refine` opens a small keyboard menu of detected directories under `specs/`. Use Up/Down or `j`/`k`, then Enter.

The built-in refiner is deterministic. It parses `T001`-style tasks, groups them by section heading, preserves every task exactly once, creates sequential dependencies, guesses file scopes from paths mentioned in tasks, and excludes Spec Kit source files by default.

### `validate`

Checks required artifacts, `epics.yaml` schema, task coverage, duplicate task assignment, dependency validity, cycles, validation presence, adapter registration, and git availability.

```bash
speckit-orchestra validate specs/001-user-auth
speckit-orchestra validate
```

If `feature` is omitted, `validate` uses the same detected-feature selector.

### `run`

Runs pending epics in topological order.

```bash
speckit-orchestra run specs/001-user-auth --commit never
speckit-orchestra run specs/001-user-auth --dry-run
speckit-orchestra run specs/001-user-auth --from EPIC-003
speckit-orchestra run specs/001-user-auth --only EPIC-004
speckit-orchestra run --dry-run
```

If `feature` is omitted, `run`, `resume`, `status`, and `report` prompt with the detected-feature selector. Non-interactive shells still need an explicit feature path.

Useful options:

- `--allow-dirty` skips the clean-worktree preflight.
- `--max-retries <n>` overrides retry count.
- `--no-tests` skips validation commands.
- `--global-validation` also runs configured global validation commands.
- `--continue-on-blocker` continues independent epics where possible.
- `--force-unlock` clears a stale feature lock.

### `doctor`

Checks adapter readiness.

```bash
speckit-orchestra doctor --agent opencode
speckit-orchestra doctor --config-dir .spec-orchestra --skip-smoke
```

Doctor reports the current CLI version, executable path, Python path, project config schema version, project-local CLI metadata, and any `sko`, `speckit-orchestra`, or `orchestra` executables found on `PATH`. It warns when project metadata is missing or was last migrated by an older CLI version.

For `opencode`, doctor also verifies command availability, attempts `opencode --version`, and runs a harmless non-interactive smoke prompt unless `--skip-smoke` is used.

### Version and update

```bash
sko --version
sko --update
sko migrate --dry-run
sko migrate
```

`--version` prints the installed package version. `--update` runs `python -m pip install --upgrade speckit-orchestra` in the current Python environment and reports the version before and after the upgrade.

Run `sko migrate` from an initialized project after updating the CLI. It normalizes `.spec-orchestra/config.yaml` to the current schema, records the CLI version that last migrated the project, refreshes runtime state summaries, and writes backups under `.spec-orchestra/migrations/<timestamp>/` before changing files. Use `--dry-run` to preview changes, `--no-backup` to skip backups, or `--config-dir <path>` for a non-default orchestration directory.

## opencode Model Configuration

The opencode adapter builds an invocation from first-class config fields and then passes the prompt over stdin.

```yaml
agent:
  adapter: opencode
  mode: cli
  command: opencode
  args:
    - run
  provider: openai
  model: gpt-5.5
  variant: high
  opencodeAgent: build
  thinking: false
  promptInput: stdin
  outputFormat: text
  timeoutMs: 1800000
```

This renders roughly as:

```bash
opencode run --model openai/gpt-5.5 --variant high --agent build
```

You can still use `agent.args` as an escape hatch. If `agent.args` already contains `--model`, `--variant`, or `--agent`, the adapter does not add duplicate flags.

Keep these settings outside the prompt. Prompt text can request behavior, but provider routing, model selection, reasoning effort, and agent choice are runtime invocation settings and should be configured explicitly.

### `adapters`

Lists bundled adapters.

```bash
speckit-orchestra adapters
```

## Runtime Artifacts

Runtime files are stored under `.spec-orchestra/` and are ignored by git by default:

```text
.spec-orchestra/
  config.yaml
  features/<feature-id>/
    epics.yaml
    state.json
    events.jsonl
    lock.json
    reports/summary.md
    runs/<epic-id>/attempt-001/
      prompt.md
      stdout.log
      stderr.log
      exit.json
      changed-files.txt
      diff.patch
      validation.log
      result.json
      result.md
```

If your team wants to review generated epics in git, remove `.spec-orchestra/` from `.gitignore` or add a narrower ignore rule for only run artifacts.

## Commit Behavior

Commit mode defaults to `ask` in generated config.

- `ask` prompts before committing when stdin is interactive; non-interactive runs skip commits.
- `auto` commits after adapter success, scope checks, and validation success.
- `never` never commits.

The default commit message includes the feature ID, epic ID, task IDs, validation summary, and a `Generated-by: speckit-orchestra` trailer.

## Development

Set up the local environment:

```bash
uv sync
```

Run tests:

```bash
uv run pytest
```

Compile-check source files:

```bash
uv run python -m compileall src tests
```

Run the CLI from source:

```bash
uv run speckit-orchestra --help
```

### Versioning requirement

Before completing any change that affects the installed package, bump the version in both places:

- `pyproject.toml` `[project].version`
- `src/speckit_orchestra/__init__.py` `__version__`

This is mandatory for CLI behavior, command flags, prompts, adapters, validation rules, migrations, generated artifact schemas, runtime behavior, dependency changes, packaging metadata, and user-facing documentation that describes installed behavior. If unsure, use a patch bump.

Build a distributable package:

```bash
uv build
```

## Current MVP Limits

- Execution is sequential only.
- The only bundled harness adapter is `opencode` CLI mode.
- The deterministic refiner does not call a model API; edit generated `epics.yaml` for project-specific grouping and validation commands.
- Failed attempt changes are preserved by default for inspection.
- Manual approval epics require an interactive terminal.
