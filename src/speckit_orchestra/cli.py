from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import yaml

from .adapters import ADAPTERS, get_adapter
from .config import default_config, load_config, write_config
from .epics import write_epics
from .feature import load_feature_artifacts
from .orchestrator import RunOptions, run_feature
from .refinement import generate_epic_document
from .reporting import render_summary_report, write_summary_report
from .state import load_state
from .utils import find_repo_root, relpath
from .validation import epics_path, feature_state_dir, validate_feature


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    root = find_repo_root(Path.cwd())
    try:
        return args.func(args, root)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="speckit-orchestra", description="Orchestrate Spec Kit tasks through AI agent CLIs.")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Initialize .spec-orchestra/config.yaml")
    init.add_argument("--agent", default="opencode")
    init.add_argument("--mode", default="cli")
    init.add_argument("--config-dir", default=".spec-orchestra")
    init.add_argument("--commit-mode", choices=["auto", "ask", "never"], default="ask")
    init.add_argument("--run-mode", choices=["sequential"], default="sequential")
    init.add_argument("--yes", action="store_true", help="Accept defaults and overwrite prompts")
    init.set_defaults(func=cmd_init)

    refine = sub.add_parser("refine", help="Generate epics.yaml from Spec Kit artifacts")
    refine.add_argument("feature")
    refine.add_argument("--output")
    refine.add_argument("--force", action="store_true")
    refine.add_argument("--dry-run", action="store_true")
    refine.add_argument("--agent")
    refine.add_argument("--interactive", action="store_true")
    refine.add_argument("--no-interactive", action="store_true")
    refine.set_defaults(func=cmd_refine)

    validate = sub.add_parser("validate", help="Validate config, feature artifacts, and epics.yaml")
    validate.add_argument("feature")
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="Run all pending epics or one epic")
    _add_run_args(run)
    run.set_defaults(func=cmd_run)

    resume = sub.add_parser("resume", help="Resume a blocked or interrupted run")
    resume.add_argument("feature")
    resume.add_argument("--allow-dirty", action="store_true")
    resume.add_argument("--no-tests", action="store_true")
    resume.add_argument("--max-retries", type=int)
    resume.add_argument("--commit", choices=["auto", "ask", "never"])
    resume.add_argument("--force-unlock", action="store_true")
    resume.set_defaults(func=cmd_resume)

    status = sub.add_parser("status", help="Show feature execution status")
    status.add_argument("feature")
    status.set_defaults(func=cmd_status)

    report = sub.add_parser("report", help="Generate and print a feature report")
    report.add_argument("feature")
    report.set_defaults(func=cmd_report)

    doctor = sub.add_parser("doctor", help="Check environment readiness")
    doctor.add_argument("--agent", default=None)
    doctor.add_argument("--skip-smoke", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    adapters = sub.add_parser("adapters", help="List available adapters")
    adapters.set_defaults(func=cmd_adapters)
    return parser


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("feature")
    parser.add_argument("epic", nargs="?")
    parser.add_argument("--agent")
    parser.add_argument("--mode")
    parser.add_argument("--commit", choices=["auto", "ask", "never"])
    parser.add_argument("--max-retries", type=int)
    parser.add_argument("--only")
    parser.add_argument("--from", dest="from_epic")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--allow-dirty", action="store_true")
    parser.add_argument("--no-tests", action="store_true")
    parser.add_argument("--global-validation", action="store_true")
    parser.add_argument("--continue-on-blocker", action="store_true")
    parser.add_argument("--revert-on-blocker", action="store_true", help="Accepted for compatibility; failed changes are preserved in MVP")
    parser.add_argument("--force-unlock", action="store_true")


def cmd_init(args, root: Path) -> int:
    config = default_config(root, agent=args.agent, mode=args.mode, config_dir=args.config_dir, commit_mode=args.commit_mode)
    config.execution.mode = args.run_mode
    path = root / args.config_dir / "config.yaml"
    if path.exists() and not args.yes:
        print(f"exists: {relpath(path, root)}")
        return 0
    written = write_config(root, config)
    print(relpath(written, root))
    return 0


def cmd_refine(args, root: Path) -> int:
    config = load_config(root)
    doc = generate_epic_document(root, args.feature, config, agent=args.agent)
    data = doc.model_dump(mode="json")
    if args.dry_run:
        print(yaml.safe_dump(data, sort_keys=False, allow_unicode=False))
        return 0
    output = Path(args.output) if args.output else epics_path(root, config, doc.feature.id)
    if not output.is_absolute():
        output = root / output
    if output.exists() and not args.force:
        print(f"error: {relpath(output, root)} already exists; use --force to overwrite", file=sys.stderr)
        return 2
    write_epics(output, doc)
    print(relpath(output, root))
    report = validate_feature(root, args.feature, config)
    if not report.ok:
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


def cmd_validate(args, root: Path) -> int:
    config = load_config(root)
    report = validate_feature(root, args.feature, config)
    for warning in report.warnings:
        print(f"warning: {warning}")
    if not report.ok:
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    print("Validation passed.")
    return 0


def cmd_run(args, root: Path) -> int:
    config = load_config(root)
    only = args.only or args.epic
    return run_feature(
        root,
        args.feature,
        config,
        RunOptions(
            only=only,
            from_epic=args.from_epic,
            dry_run=args.dry_run,
            allow_dirty=args.allow_dirty,
            no_tests=args.no_tests,
            global_validation=args.global_validation,
            max_retries=args.max_retries,
            commit_mode=args.commit,
            agent=args.agent,
            mode=args.mode,
            continue_on_blocker=args.continue_on_blocker,
            force_unlock=args.force_unlock,
        ),
    )


def cmd_resume(args, root: Path) -> int:
    config = load_config(root)
    return run_feature(
        root,
        args.feature,
        config,
        RunOptions(
            allow_dirty=args.allow_dirty,
            no_tests=args.no_tests,
            max_retries=args.max_retries,
            commit_mode=args.commit,
            force_unlock=args.force_unlock,
            resume=True,
        ),
    )


def cmd_status(args, root: Path) -> int:
    config = load_config(root)
    artifacts = load_feature_artifacts(root, args.feature)
    path = epics_path(root, config, artifacts.id)
    if not path.exists():
        print(f"No epics.yaml found for {artifacts.id}.")
        return 1
    from .epics import load_epics

    doc = load_epics(path)
    feature_dir = feature_state_dir(root, config, artifacts.id)
    state = load_state(feature_dir, doc.feature.path, config, doc)
    print(f"Feature: {state['featureId']}")
    print(f"Status: {state['status']}")
    adapter = state.get("adapter", {})
    if isinstance(adapter, dict):
        print(f"Adapter: {adapter.get('name')} {adapter.get('mode')}")
    print("")
    for title, wanted in (("Completed", "complete"), ("Blocked", "blocked"), ("Pending", None)):
        print(f"{title}:")
        count = 0
        for epic in doc.epics:
            item = state.get("epics", {}).get(epic.id, {})
            status = item.get("status", "pending")
            if (wanted and status == wanted) or (wanted is None and status not in {"complete", "blocked"}):
                count += 1
                suffix = f" {item.get('commit')}" if item.get("commit") else ""
                print(f"  {epic.id} {epic.title}{suffix}")
                blocker = item.get("blocker")
                if isinstance(blocker, dict):
                    print(f"    {blocker.get('category')}: {blocker.get('message')}")
        if count == 0:
            print("  None")
        print("")
    return 0


def cmd_report(args, root: Path) -> int:
    config = load_config(root)
    artifacts = load_feature_artifacts(root, args.feature)
    from .epics import load_epics

    doc = load_epics(epics_path(root, config, artifacts.id))
    feature_dir = feature_state_dir(root, config, artifacts.id)
    state = load_state(feature_dir, doc.feature.path, config, doc)
    path = write_summary_report(feature_dir, doc, state)
    print(render_summary_report(doc, state))
    print(f"Report written to {relpath(path, root)}")
    return 0


def cmd_doctor(args, root: Path) -> int:
    config = load_config(root)
    if args.agent:
        config.agent.adapter = args.agent
        config.agent.command = args.agent
    adapter = get_adapter(config.agent.adapter)
    if adapter is None:
        print(f"error: unknown adapter {config.agent.adapter}", file=sys.stderr)
        return 2
    checks = _generic_doctor_checks(root) + adapter.doctor(config, root, smoke=not args.skip_smoke)
    ok = True
    for check in checks:
        marker = "ok" if check["ok"] else "fail"
        print(f"[{marker}] {check['name']}: {check['detail']}")
        ok = ok and bool(check["ok"])
    return 0 if ok else 3


def cmd_adapters(args, root: Path) -> int:
    for name, adapter in ADAPTERS.items():
        print(f"{name}\tmode={adapter.mode}")
    return 0


def _generic_doctor_checks(root: Path) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    checks.append(
        {
            "name": "Python version",
            "ok": sys.version_info >= (3, 11),
            "detail": sys.version.split()[0],
        }
    )
    git_path = shutil.which("git")
    checks.append(
        {
            "name": "git command",
            "ok": git_path is not None,
            "detail": git_path or "git was not found on PATH",
        }
    )
    if git_path:
        repo = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        checks.append(
            {
                "name": "git repository",
                "ok": repo.returncode == 0,
                "detail": "inside a git repository" if repo.returncode == 0 else (repo.stderr.strip() or "not inside a git repository"),
            }
        )
    node_path = shutil.which("node")
    if node_path:
        version = subprocess.run(["node", "--version"], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        detail = (version.stdout or version.stderr).strip() or node_path
    else:
        detail = "not found; only required if your adapter installation needs Node.js"
    checks.append({"name": "Node.js version", "ok": True, "detail": detail})
    return checks
