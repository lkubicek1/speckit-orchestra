from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shlex
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import PurePosixPath

from . import git
from .adapters import get_adapter
from .config import Config
from .epics import Epic, load_epics
from .feature import load_feature_artifacts, parse_tasks
from .locks import LockError, acquire_lock, release_lock
from .prompts import dependency_summary_for, render_attempt_report, render_epic_prompt
from .reporting import write_summary_report
from .state import append_event, load_state, mark_feature_running, reset_blocked_for_resume, save_state
from .ui import progress_label, progress_spinner
from .utils import atomic_write_json, atomic_write_text, now_iso, relpath
from .validation import epics_path, feature_state_dir, topological_epics, validate_feature


@dataclass
class RunOptions:
    only: str | None = None
    from_epic: str | None = None
    dry_run: bool = False
    allow_dirty: bool = False
    no_tests: bool = False
    global_validation: bool = False
    max_retries: int | None = None
    validation_retries: int | None = None
    validation_timeout_ms: int | None = None
    commit_mode: str | None = None
    agent: str | None = None
    mode: str | None = None
    continue_on_blocker: bool = False
    force_unlock: bool = False
    resume: bool = False


def run_feature(root, feature: str, config: Config, options: RunOptions) -> int:
    if options.agent:
        config.agent.adapter = options.agent
        config.agent.command = options.agent
    if options.mode:
        config.agent.mode = options.mode
    if options.max_retries is not None:
        config.execution.maxRetries = options.max_retries
    if options.validation_retries is not None:
        config.execution.validationRetries = options.validation_retries
    if options.validation_timeout_ms is not None:
        config.validation.commandTimeoutMs = options.validation_timeout_ms
    if options.commit_mode:
        config.commit.mode = options.commit_mode
    if options.continue_on_blocker:
        config.execution.continueOnBlocker = True

    readiness = validate_feature(root, feature, config)
    if not readiness.ok:
        for error in readiness.errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    doc = readiness.epics
    assert doc is not None

    if config.execution.requireCleanGit and not options.allow_dirty and not options.dry_run:
        if git.has_conflicts(root):
            print("error: git conflict markers or unmerged paths are present", file=sys.stderr)
            return 4
        dirty_paths = _dirty_paths_for_run_preflight(root, config)
        if dirty_paths:
            print(
                "error: working tree is dirty; use --allow-dirty to override\n" + _format_paths(dirty_paths),
                file=sys.stderr,
            )
            return 4

    feature_dir = feature_state_dir(root, config, doc.feature.id)
    lock_path = feature_dir / "lock.json"
    if options.dry_run:
        return _print_dry_run(doc, options)

    try:
        acquire_lock(lock_path, "speckit-orchestra run", force=options.force_unlock)
    except LockError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 5

    try:
        state = load_state(feature_dir, doc.feature.path, config, doc)
        if options.resume:
            reset_blocked_for_resume(state)
        mark_feature_running(state)
        save_state(feature_dir, state)
        append_event(feature_dir, "feature.started", featureId=doc.feature.id)

        result = _run_loop(root, feature, config, doc, state, feature_dir, options)
        write_summary_report(feature_dir, doc, state)
        save_state(feature_dir, state)
        return result
    finally:
        release_lock(lock_path)


def _run_loop(root, feature: str, config: Config, doc, state, feature_dir, options: RunOptions) -> int:
    order = topological_epics(doc)
    epic_by_id = {epic.id: epic for epic in doc.epics}
    targets = _target_order(order, options)
    if not targets:
        print("No epics selected.")
        return 0

    while True:
        if _all_complete(state, targets):
            if _all_complete(state, order):
                state["status"] = "complete"
                append_event(feature_dir, "feature.completed", featureId=doc.feature.id)
            save_state(feature_dir, state)
            print("All selected epics are complete.")
            return 0

        epic_id = _next_runnable(state, targets, epic_by_id)
        if not epic_id:
            blocker = {
                "category": "unknown",
                "message": "No runnable epic remains. Check dependency and blocker state.",
                "suggestedNextAction": "Run `speckit-orchestra status` and resolve blocked dependencies.",
            }
            state["status"] = "blocked"
            state["blocker"] = blocker
            save_state(feature_dir, state)
            append_event(feature_dir, "feature.blocked", featureId=doc.feature.id, category="unknown")
            print(f"Blocked: {blocker['message']}", file=sys.stderr)
            return 1

        epic = epic_by_id[epic_id]
        if epic.approval.required and not _approved(epic):
            _mark_blocked(state, feature_dir, epic.id, "manual_review_required", epic.approval.reason or "approval required")
            if not config.execution.continueOnBlocker:
                return 1
            continue

        position = targets.index(epic_id) + 1
        status = _run_epic(root, feature, config, doc, state, feature_dir, epic, options, position, len(targets))
        if status == 0:
            continue
        if config.execution.continueOnBlocker:
            continue
        return status


def _run_epic(root, feature: str, config: Config, doc, state, feature_dir, epic: Epic, options: RunOptions, position: int, total: int) -> int:
    adapter = get_adapter(config.agent.adapter)
    if adapter is None:
        _mark_blocked(state, feature_dir, epic.id, "agent_error", f"unknown adapter {config.agent.adapter}")
        return 3

    artifacts = load_feature_artifacts(root, feature)
    all_tasks = parse_tasks(artifacts.tasks.read_text(encoding="utf-8"))
    validation_failure: str | None = None
    validation_failure_evidence: list[str] = []
    general_max_attempts = max(1, config.execution.maxRetries + 1)
    validation_max_attempts = max(1, config.execution.validationRetries + 1)
    max_attempts = max(general_max_attempts, validation_max_attempts)

    while state["epics"][epic.id].get("attempts", 0) < max_attempts:
        attempt = int(state["epics"][epic.id].get("attempts", 0)) + 1
        print(progress_label("Working on", position, total, epic.id, epic.title) + f" (attempt {attempt}/{max_attempts})")
        state["currentEpic"] = epic.id
        state["epics"][epic.id]["status"] = "running"
        state["epics"][epic.id]["attempts"] = attempt
        save_state(feature_dir, state)
        append_event(feature_dir, "epic.started", epicId=epic.id, attempt=attempt)

        attempt_dir = feature_dir / "runs" / epic.id / f"attempt-{attempt:03d}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        prompt = render_epic_prompt(
            root=root,
            artifacts=artifacts,
            epic=epic,
            tasks=all_tasks,
            dependency_summary=dependency_summary_for(epic, state),
            validation_failure=validation_failure,
        )
        atomic_write_text(attempt_dir / "prompt.md", prompt)

        before_head = git.head(root)
        before_status = git.status_porcelain(root)
        before_snapshot = _snapshot_status_paths(root, config, doc.feature.id, before_status)
        invocation = adapter.build_invocation(config, root, prompt)
        with progress_spinner(progress_label("Working on", position, total, epic.id, epic.title)):
            result = adapter.run(invocation, attempt_dir / "stdout.log", attempt_dir / "stderr.log")
        atomic_write_json(
            attempt_dir / "exit.json",
            {
                "beforeHead": before_head,
                "beforeStatus": before_status,
                "command": invocation.command,
                "args": invocation.args,
                "exitCode": result.exit_code,
                "status": result.status,
                "finishedAt": now_iso(),
            },
        )
        if result.status != "complete":
            _write_attempt_result(attempt_dir, epic, attempt, result.status, result.exit_code, [], "", result.blocker)
            _mark_blocked_from_result(state, feature_dir, epic.id, result.blocker or _blocker("agent_error", result.summary))
            return 3

        changed = _attempt_changed_files(root, config, doc.feature.id, before_snapshot)
        atomic_write_text(attempt_dir / "changed-files.txt", "\n".join(changed) + ("\n" if changed else ""))
        atomic_write_text(attempt_dir / "diff.patch", git.diff_patch(root))

        blocker = _scope_blocker(epic, changed)
        if blocker:
            _write_attempt_result(attempt_dir, epic, attempt, result.status, result.exit_code, changed, "", blocker)
            _mark_blocked_from_result(state, feature_dir, epic.id, blocker)
            return 1

        if not changed and not _allows_no_changes(epic):
            blocker = _no_changes_blocker(root, attempt_dir, validation_failure, validation_failure_evidence)
            _write_attempt_result(
                attempt_dir,
                epic,
                attempt,
                result.status,
                result.exit_code,
                changed,
                validation_failure or "",
                blocker,
            )
            if attempt < general_max_attempts:
                validation_failure = blocker["message"]
                validation_failure_evidence = list(blocker.get("evidence", []))
                state["epics"][epic.id]["status"] = "retrying"
                save_state(feature_dir, state)
                continue
            _mark_blocked_from_result(state, feature_dir, epic.id, blocker)
            return 1

        validation_ok, validation_summary = _run_validation(root, config, epic, attempt_dir, options)
        if not validation_ok:
            blocker = _blocker(
                "validation_failed",
                f"Validation failed for {epic.id} attempt {attempt}.",
                "Inspect validation.log, fix the cause, then run `speckit-orchestra resume`.",
                [relpath(attempt_dir / "validation.log", root)],
            )
            _write_attempt_result(attempt_dir, epic, attempt, result.status, result.exit_code, changed, validation_summary, blocker)
            if attempt < validation_max_attempts:
                validation_failure = validation_summary
                validation_failure_evidence = list(blocker.get("evidence", []))
                state["epics"][epic.id]["status"] = "retrying"
                append_event(feature_dir, "epic.retrying", epicId=epic.id, attempt=attempt, category="validation_failed")
                save_state(feature_dir, state)
                continue
            _mark_blocked_from_result(state, feature_dir, epic.id, blocker)
            return 1

        commit = _maybe_commit(root, config, doc.feature.path, epic, changed, validation_summary)
        state["epics"][epic.id]["status"] = "complete"
        state["epics"][epic.id]["completedAt"] = now_iso()
        if commit:
            state["epics"][epic.id]["commit"] = commit
            append_event(feature_dir, "epic.committed", epicId=epic.id, commit=commit)
        state["lastCompletedEpic"] = epic.id
        state["currentEpic"] = None
        save_state(feature_dir, state)
        append_event(feature_dir, "epic.completed", epicId=epic.id, attempt=attempt)
        _write_attempt_result(attempt_dir, epic, attempt, result.status, result.exit_code, changed, validation_summary, None)
        print(progress_label("Completed", position, total, epic.id, epic.title))
        return 0

    blocker = _blocker("validation_failed", f"{epic.id} failed after {max_attempts} attempts.")
    _mark_blocked_from_result(state, feature_dir, epic.id, blocker)
    return 1


def _run_validation(root, config: Config, epic: Epic, attempt_dir, options: RunOptions) -> tuple[bool, str]:
    commands: list[str] = [] if options.no_tests else list(epic.validation.commands)
    if options.global_validation and not options.no_tests:
        commands.extend(config.validation.globalCommands)
    if not commands:
        manual = "\n".join(f"MANUAL: {check}" for check in epic.validation.manualChecks)
        summary = manual or "No validation commands configured."
        atomic_write_text(attempt_dir / "validation.log", summary + "\n")
        return True, summary

    log_parts: list[str] = []
    ok = True
    for command in commands:
        log_parts.append(f"$ {command}\n")
        result = _run_validation_command(command, root, config.validation.commandTimeoutMs)
        log_parts.append(result.stdout)
        log_parts.append(result.stderr)
        if result.timed_out:
            log_parts.append(f"\ntimed out after {config.validation.commandTimeoutMs}ms\n")
        log_parts.append(f"\nexit code: {result.returncode}\n")
        if result.returncode != 0 or result.timed_out:
            if epic.validation.expectedFailureAllowed:
                log_parts.append("failure accepted because expectedFailureAllowed is true\n")
            else:
                ok = False
    summary = "".join(log_parts)
    atomic_write_text(attempt_dir / "validation.log", summary)
    return ok, summary


@dataclass(frozen=True)
class ValidationCommandResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


def _run_validation_command(command: str, root, timeout_ms: int) -> ValidationCommandResult:
    process = subprocess.Popen(
        command,
        cwd=root,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=max(timeout_ms, 1) / 1000)
        return ValidationCommandResult(process.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        _terminate_validation_process(process)
        stdout, stderr = process.communicate()
        return ValidationCommandResult(process.returncode if process.returncode is not None else -signal.SIGTERM, stdout, stderr, True)


def _terminate_validation_process(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except Exception:
        process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except Exception:
            process.kill()


def _maybe_commit(root, config: Config, spec_path: str, epic: Epic, changed: list[str], validation_summary: str) -> str | None:
    mode = config.commit.mode
    if mode == "never" or not changed:
        return None
    if mode == "ask":
        if not sys.stdin.isatty():
            return None
        answer = input(f"Commit {epic.id} changes? [y/N] ").strip().lower()
        if answer not in {"y", "yes"}:
            return None
    message = config.commit.messageTemplate.format(
        featureId=spec_path.split("/")[-1],
        epicId=epic.id,
        epicTitle=epic.title,
        specPath=spec_path,
        taskIds=", ".join(epic.tasks),
        validationSummary=_short_validation(validation_summary),
    )
    return git.commit_changes(root, changed, message)


def _short_validation(text: str) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-20:]) or "No automated validation run."


def _scope_blocker(epic: Epic, changed: list[str]) -> dict[str, object] | None:
    for path in changed:
        if _matches_any(path, epic.scope.exclude):
            return _blocker("scope_violation", f"Forbidden path changed: {path}", "Revert or move the change, then resume.")
        if not _matches_any(path, epic.scope.include):
            return _blocker("scope_violation", f"Changed path is outside scope.include: {path}", "Update epics.yaml scope or revert the out-of-scope change.")
    return None


def _matches_any(path: str, patterns: list[str]) -> bool:
    normalized = path.strip("/")
    for pattern in patterns:
        pat = pattern.strip("/")
        if pat in {"**", "**/*"}:
            return True
        if pat.endswith("/**"):
            prefix = pat[:-3].strip("/")
            if normalized == prefix or normalized.startswith(prefix + "/"):
                return True
        if fnmatch.fnmatchcase(normalized, pat) or PurePosixPath(normalized).match(pat):
            return True
    return False


def _dirty_paths_for_run_preflight(root, config: Config) -> list[str]:
    return [path for path in git.changed_files(root) if not _is_orchestra_project_artifact(path, config)]


def _attempt_changed_files(root, config: Config, feature_id: str, before_snapshot: dict[str, str | None]) -> list[str]:
    after_status = git.status_porcelain(root)
    return _changed_paths_since_snapshot(root, before_snapshot, after_status, config, feature_id)


def _changed_paths_since_status(before_status: str, after_status: str, config: Config, feature_id: str) -> list[str]:
    before_snapshot = {path: None for path in _status_paths(before_status)}
    after = _status_paths(after_status)
    changed = sorted(after - set(before_snapshot))
    return [path for path in changed if not _is_orchestra_runtime_artifact(path, config, feature_id)]


def _changed_paths_since_snapshot(root, before_snapshot: dict[str, str | None], after_status: str, config: Config, feature_id: str) -> list[str]:
    after_paths = _status_paths(after_status)
    candidates = sorted(after_paths | set(before_snapshot))
    changed: list[str] = []
    for path in candidates:
        if _is_orchestra_runtime_artifact(path, config, feature_id):
            continue
        before = before_snapshot.get(path)
        after = _file_fingerprint(root, path) if path in after_paths else None
        if path not in before_snapshot or before != after:
            changed.append(path)
    return changed


def _snapshot_status_paths(root, config: Config, feature_id: str, status: str) -> dict[str, str | None]:
    snapshot: dict[str, str | None] = {}
    for path in _status_paths(status):
        if not _is_orchestra_runtime_artifact(path, config, feature_id):
            snapshot[path] = _file_fingerprint(root, path)
    return snapshot


def _file_fingerprint(root, path: str) -> str | None:
    file_path = root / path
    if not file_path.is_file():
        return None
    digest = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _status_paths(status: str) -> set[str]:
    paths: set[str] = set()
    for line in status.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        if path:
            paths.add(path)
    return paths


def _is_orchestra_project_artifact(path: str, config: Config) -> bool:
    root = config.project.orchestraRoot.strip("/")
    return path == root or path.startswith(f"{root}/")


def _is_orchestra_runtime_artifact(path: str, config: Config, feature_id: str) -> bool:
    root = config.project.orchestraRoot.strip("/")
    feature_prefix = f"{root}/features/{feature_id}/"
    if path.startswith(f"{root}/migrations/"):
        return True
    if not path.startswith(feature_prefix):
        return False
    relative = path[len(feature_prefix) :]
    return relative in {"state.json", "events.jsonl", "lock.json"} or relative.startswith(("runs/", "reports/"))


def _format_paths(paths: list[str]) -> str:
    shown = paths[:10]
    suffix = "" if len(paths) <= len(shown) else f"\n... and {len(paths) - len(shown)} more"
    return "\n".join(f"  {path}" for path in shown) + suffix


def _allows_no_changes(epic: Epic) -> bool:
    text = f"{epic.title} {epic.goal}".lower()
    return "manual" in text or "documentation" in text or "docs" in text


def _no_changes_blocker(
    root,
    attempt_dir,
    validation_failure: str | None = None,
    validation_failure_evidence: list[str] | None = None,
) -> dict[str, object]:
    stdout_path = attempt_dir / "stdout.log"
    stdout_evidence = relpath(stdout_path, root)
    evidence = [*(validation_failure_evidence or []), stdout_evidence]
    rationale = _stdout_rationale(stdout_path)
    if validation_failure:
        message = "Validation failed previously and the adapter completed this attempt without changing files."
        if rationale:
            message = f"{message} Adapter output: {rationale}"
        return _blocker(
            "validation_failed",
            message,
            "Inspect validation.log and stdout.log, clarify the failing requirement, then resume.",
            evidence,
        )

    message = "Adapter completed but did not change any files."
    if rationale:
        message = f"{message} Adapter output: {rationale}"
    return _blocker(
        "no_changes",
        message,
        "Inspect stdout.log and rerun after clarifying the epic scope.",
        [stdout_evidence],
    )


def _stdout_rationale(stdout_path) -> str | None:
    if not stdout_path.exists():
        return None
    lines = [line.strip() for line in stdout_path.read_text(encoding="utf-8", errors="replace").splitlines()]
    lines = [line for line in lines if line]
    if not lines:
        return None
    selected = lines[-3:]
    text = " ".join(selected)
    return text[:500]


def _write_attempt_result(attempt_dir, epic, attempt, adapter_status, exit_code, changed, validation_summary, blocker) -> None:
    atomic_write_json(
        attempt_dir / "result.json",
        {
            "epicId": epic.id,
            "attempt": attempt,
            "adapterStatus": adapter_status,
            "exitCode": exit_code,
            "changedFiles": changed,
            "validationSummary": validation_summary,
            "blocker": blocker,
        },
    )
    atomic_write_text(
        attempt_dir / "result.md",
        render_attempt_report(
            epic=epic,
            attempt=attempt,
            adapter_status=adapter_status,
            exit_code=exit_code,
            changed_files=changed,
            validation_summary=validation_summary,
            blocker=blocker,
        ),
    )


def _mark_blocked(state, feature_dir, epic_id: str, category: str, message: str) -> None:
    _mark_blocked_from_result(state, feature_dir, epic_id, _blocker(category, message))


def _mark_blocked_from_result(state, feature_dir, epic_id: str, blocker: dict[str, object]) -> None:
    state["status"] = "blocked"
    state["currentEpic"] = epic_id
    state["epics"][epic_id]["status"] = "blocked"
    state["epics"][epic_id]["blockedAt"] = now_iso()
    state["epics"][epic_id]["blocker"] = blocker
    state["blocker"] = blocker
    save_state(feature_dir, state)
    append_event(feature_dir, "feature.blocked", epicId=epic_id, category=blocker.get("category", "unknown"))
    print(f"Blocked {epic_id}: {blocker.get('message', '')}", file=sys.stderr)


def _blocker(category: str, message: str, next_action: str | None = None, evidence: list[str] | None = None) -> dict[str, object]:
    data: dict[str, object] = {"category": category, "message": message}
    if next_action:
        data["suggestedNextAction"] = next_action
    if evidence:
        data["evidence"] = evidence
    return data


def _approved(epic: Epic) -> bool:
    if not sys.stdin.isatty():
        return False
    reason = f" Reason: {epic.approval.reason}" if epic.approval.reason else ""
    answer = input(f"{epic.id} requires approval.{reason} Continue? [y/N] ").strip().lower()
    return answer in {"y", "yes"}


def _all_complete(state, epic_ids: list[str]) -> bool:
    return all(state["epics"].get(epic_id, {}).get("status") == "complete" for epic_id in epic_ids)


def _next_runnable(state, targets: list[str], epic_by_id: dict[str, Epic]) -> str | None:
    for epic_id in targets:
        if state["epics"].get(epic_id, {}).get("status") == "complete":
            continue
        epic = epic_by_id[epic_id]
        deps_complete = all(state["epics"].get(dep, {}).get("status") == "complete" for dep in epic.dependencies)
        if deps_complete:
            return epic_id
    return None


def _target_order(order: list[str], options: RunOptions) -> list[str]:
    targets = order
    if options.from_epic:
        if options.from_epic not in targets:
            raise ValueError(f"unknown epic for --from: {options.from_epic}")
        targets = targets[targets.index(options.from_epic) :]
    if options.only:
        if options.only not in order:
            raise ValueError(f"unknown epic for --only: {options.only}")
        targets = [options.only]
    return targets


def _print_dry_run(doc, options: RunOptions) -> int:
    order = topological_epics(doc)
    targets = _target_order(order, options)
    print("Execution plan:")
    for epic_id in targets:
        epic = next(epic for epic in doc.epics if epic.id == epic_id)
        deps = ", ".join(epic.dependencies) or "none"
        commands = "; ".join(shlex.quote(cmd) for cmd in epic.validation.commands) or "manual checks"
        approval = " | approval: required" if epic.approval.required else ""
        print(f"- {epic.id}: {epic.title} | deps: {deps} | validation: {commands}{approval}")
    if any(next(epic for epic in doc.epics if epic.id == epic_id).approval.required for epic_id in targets):
        print("warning: one or more selected epics require an interactive approval prompt before execution")
    return 0
