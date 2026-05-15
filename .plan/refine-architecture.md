# Refine Architecture Notes

## Direction

`sko refine` should evolve into an agent-first epic generation step with a deterministic heuristic fallback.

The agent should own project-specific interpretation: how Spec Kit tasks map into coherent epics, what validation commands make sense for the stack, and where edge cases require nuance. The orchestrator should own the contract: schema validation, task coverage, scope enforcement, dependency validity, state transitions, retries, timeouts, logs, and commits.

## Target Responsibility Split

Agent-authored refine should:

- Read `spec.md`, `plan.md`, `tasks.md`, and optional feature artifacts such as `quickstart.md`.
- Generate `epics.yaml` from the actual project context.
- Choose validation commands or manual checks for each epic.
- Define scopes that are tight enough to protect the project but broad enough for the task.
- Explain blockers when validation or scope requires human clarification.

The orchestrator should:

- Treat generated `epics.yaml` as an execution contract.
- Validate schema and reject malformed output.
- Require all tasks to be covered exactly once or explicitly excluded.
- Require valid, acyclic dependencies.
- Require each epic to declare validation commands or manual checks.
- Enforce declared scopes during execution.
- Run validation commands opaquely, without framework-specific knowledge.
- Apply generic guardrails such as retries, per-command timeouts, logs, and blocked state.

The orchestrator should not decide that a project is a web app, Python package, mobile app, CLI, service, or library and then bake in framework-specific validation behavior as a first-class rule. It may provide safe fallback heuristics, but those should be clearly secondary to agent-authored output.

## Proposed Modes

Default future behavior:

```bash
sko refine specs/001-feature
```

- Invokes the configured refinement agent.
- Produces `epics.yaml` using a strict output schema.
- Validates the result before writing or accepting it.

Fallback behavior:

```bash
sko refine specs/001-feature --heuristic
```

- Uses the current deterministic task-to-epic rules.
- Provides fast/offline generation.
- Remains useful for smoke tests, simple projects, and agent unavailability.

Preview behavior:

```bash
sko refine specs/001-feature --dry-run
```

- Prints the generated document without writing it.
- Should work for both agent and heuristic modes.

## Guardrail Model

Agent output is dynamic, but acceptance is strongly controlled. Generated `epics.yaml` should only be accepted after validation confirms:

- Valid schema.
- Feature paths match the current Spec Kit artifacts.
- No duplicate epic IDs.
- No missing tasks.
- No task assigned to multiple epics.
- Dependency references exist and do not form cycles.
- Each epic has non-empty acceptance criteria.
- Each epic has validation commands or manual checks.
- Scope includes and excludes are present.
- Protected Spec Kit source artifacts are excluded unless explicitly allowed by task scope.

This keeps the system flexible enough for agents to handle edge cases while preserving deterministic safety at the orchestration boundary.

## Validation Philosophy

Validation specifics belong to Spec Kit context and the refinement agent. The orchestrator should run declared validation commands as opaque shell commands and judge only generic outcomes:

- Exit code.
- Timeout.
- Captured stdout/stderr.
- Expected-failure policy.

This allows validation to be `npm run e2e`, `uv run pytest`, `cargo test`, `go test ./...`, a shell script, or manual checks without making those tools first-class concepts in the orchestrator.

## Open Design Questions

- Whether `validation.commands` should remain strings or become structured objects with fields such as `command`, `timeoutMs`, `workingDirectory`, and `description`.
- Whether agent-authored refine should replace the current heuristic default immediately or ship behind an explicit flag first.
- Whether failed agent generation should automatically fall back to heuristic generation or block with a refinement error.
- How much rationale the agent should include in `epics.yaml` versus separate refinement artifacts.
