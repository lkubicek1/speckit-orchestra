from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from importlib import metadata as importlib_metadata
from pathlib import Path

import yaml

from . import __version__
from .adapters import ADAPTERS, get_adapter
from .config import config_path, default_config, load_config, write_config
from .epics import write_epics
from .feature import discover_feature_paths, load_feature_artifacts
from .migration import CURRENT_CONFIG_VERSION, migrate_project
from .opencode_discovery import OpencodeDiscovery, discover_opencode
from .orchestrator import RunOptions, run_feature
from .project import clean_project, ensure_git_info_exclude
from .refinement import generate_epic_document
from .reporting import render_summary_report, write_summary_report
from .state import load_state
from .utils import find_repo_root, relpath
from .validation import epics_path, feature_state_dir, validate_feature


PACKAGE_NAME = "speckit-orchestra"
ANSI_BOLD = "\x1b[1m"
ANSI_DIM = "\x1b[2m"
ANSI_REVERSE = "\x1b[7m"
ANSI_RESET = "\x1b[0m"
_CUSTOM_SELECTION = object()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if args.version:
            return cmd_version(args)
        if args.update:
            return cmd_update(args)
        if not hasattr(args, "func"):
            parser.print_help(sys.stderr)
            return 2
        root = find_repo_root(Path.cwd())
        return args.func(args, root)
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="speckit-orchestra", description="Orchestrate Spec Kit tasks through AI agent CLIs.")
    parser.add_argument("--version", action="store_true", help="Print the installed version and exit")
    parser.add_argument("--update", action="store_true", help="Upgrade speckit-orchestra in the current Python environment")
    sub = parser.add_subparsers(dest="command")

    init = sub.add_parser("init", help="Initialize .spec-orchestra/config.yaml")
    init.add_argument("--agent", default="opencode")
    init.add_argument("--mode", default="cli")
    init.add_argument("--config-dir", default=".spec-orchestra")
    init.add_argument("--commit-mode", choices=["auto", "ask", "never"], default="ask")
    init.add_argument("--run-mode", choices=["sequential"], default="sequential")
    init.add_argument("--provider", help="Provider ID for opencode model selection, for example openai")
    init.add_argument("--model", help="Model ID, either provider/model or model when --provider is set")
    init.add_argument("--variant", help="Provider-specific reasoning variant, for example minimal, high, or max")
    init.add_argument("--opencode-agent", help="opencode agent name to pass with --agent")
    init.add_argument("--thinking", action="store_true", help="Pass --thinking to opencode run")
    init.add_argument("--discover", action=argparse.BooleanOptionalAction, default=None, help="Discover local opencode providers/models/agents during init")
    init.add_argument("--yes", action="store_true", help="Accept defaults and overwrite prompts")
    init.set_defaults(func=cmd_init)

    configure = sub.add_parser("configure", help="Update adapter runtime settings in .spec-orchestra/config.yaml")
    configure.add_argument("--agent", default=None)
    configure.add_argument("--mode", default=None)
    configure.add_argument("--provider")
    configure.add_argument("--model")
    configure.add_argument("--variant")
    configure.add_argument("--opencode-agent")
    configure.add_argument("--thinking", action=argparse.BooleanOptionalAction, default=None)
    configure.add_argument("--discover", action=argparse.BooleanOptionalAction, default=None, help="Discover local opencode providers/models/agents")
    configure.set_defaults(func=cmd_configure)

    refine = sub.add_parser("refine", help="Generate epics.yaml from Spec Kit artifacts")
    refine.add_argument("feature", nargs="?")
    refine.add_argument("--output")
    refine.add_argument("--force", action="store_true")
    refine.add_argument("--dry-run", action="store_true")
    refine.add_argument("--agent")
    refine.add_argument("--interactive", action="store_true")
    refine.add_argument("--no-interactive", action="store_true")
    refine.set_defaults(func=cmd_refine)

    validate = sub.add_parser("validate", help="Validate config, feature artifacts, and epics.yaml")
    validate.add_argument("feature", nargs="?")
    validate.set_defaults(func=cmd_validate)

    run = sub.add_parser("run", help="Run all pending epics or one epic")
    _add_run_args(run)
    run.set_defaults(func=cmd_run)

    resume = sub.add_parser("resume", help="Resume a blocked or interrupted run")
    resume.add_argument("feature", nargs="?")
    resume.add_argument("--allow-dirty", action="store_true")
    resume.add_argument("--no-tests", action="store_true")
    resume.add_argument("--max-retries", type=int)
    resume.add_argument("--validation-retries", type=int)
    resume.add_argument("--commit", choices=["auto", "ask", "never"])
    resume.add_argument("--force-unlock", action="store_true")
    resume.set_defaults(func=cmd_resume)

    status = sub.add_parser("status", help="Show feature execution status")
    status.add_argument("feature", nargs="?")
    status.set_defaults(func=cmd_status)

    report = sub.add_parser("report", help="Generate and print a feature report")
    report.add_argument("feature", nargs="?")
    report.set_defaults(func=cmd_report)

    migrate = sub.add_parser("migrate", help="Migrate project-local speckit-orchestra artifacts")
    migrate.add_argument("--config-dir", default=".spec-orchestra", help="Project orchestration directory")
    migrate.add_argument("--dry-run", action="store_true", help="Show migration changes without writing files")
    migrate.add_argument("--no-backup", action="store_true", help="Do not back up changed files before writing")
    migrate.set_defaults(func=cmd_migrate)

    clean = sub.add_parser("clean", help="Remove project-local speckit-orchestra artifacts")
    clean.add_argument("--config-dir", default=".spec-orchestra", help="Project orchestration directory")
    clean.add_argument("--runtime-only", action="store_true", help="Remove run state/logs while keeping config and epics")
    clean.add_argument("--dry-run", action="store_true", help="Show files that would be removed")
    clean.add_argument("--yes", action="store_true", help="Remove without an interactive confirmation")
    clean.set_defaults(func=cmd_clean)

    doctor = sub.add_parser("doctor", help="Check environment readiness")
    doctor.add_argument("--agent", default=None)
    doctor.add_argument("--config-dir", default=".spec-orchestra", help="Project orchestration directory")
    doctor.add_argument("--skip-smoke", action="store_true")
    doctor.set_defaults(func=cmd_doctor)

    adapters = sub.add_parser("adapters", help="List available adapters")
    adapters.set_defaults(func=cmd_adapters)
    return parser


def _add_run_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("feature", nargs="?")
    parser.add_argument("epic", nargs="?")
    parser.add_argument("--agent")
    parser.add_argument("--mode")
    parser.add_argument("--commit", choices=["auto", "ask", "never"])
    parser.add_argument("--max-retries", type=int)
    parser.add_argument("--validation-retries", type=int)
    parser.add_argument("--validation-timeout-ms", type=int)
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
    config = default_config(
        root,
        agent=args.agent,
        mode=args.mode,
        config_dir=args.config_dir,
        commit_mode=args.commit_mode,
        provider=args.provider,
        model=None,
        variant=args.variant,
        opencode_agent=args.opencode_agent,
        thinking=args.thinking,
    )
    _set_model(config, args.model)
    config.execution.mode = args.run_mode
    path = root / args.config_dir / "config.yaml"
    if path.exists() and not args.yes:
        print(f"exists: {relpath(path, root)}")
        return 0
    direct_agent_config = any([args.provider, args.model, args.variant, args.opencode_agent, args.thinking])
    if _should_discover(args.discover, default=(not args.yes and not direct_agent_config), config=config):
        _configure_opencode_interactive(config, root)
    written = write_config(root, config)
    ensure_git_info_exclude(root, config.project.orchestraRoot)
    print(relpath(written, root))
    return 0


def cmd_configure(args, root: Path) -> int:
    config = load_config(root)
    if args.agent:
        config.agent.adapter = args.agent
        config.agent.command = args.agent
    if args.mode:
        config.agent.mode = args.mode
    if args.provider:
        config.agent.provider = args.provider
    if args.variant:
        config.agent.variant = args.variant
    if args.opencode_agent:
        config.agent.opencodeAgent = args.opencode_agent
    if args.thinking is not None:
        config.agent.thinking = args.thinking
    _set_model(config, args.model)
    direct_agent_config = any(
        [args.agent, args.mode, args.provider, args.model, args.variant, args.opencode_agent, args.thinking is not None]
    )
    if _should_discover(args.discover, default=(not direct_agent_config), config=config):
        _configure_opencode_interactive(config, root)
    written = write_config(root, config)
    ensure_git_info_exclude(root, config.project.orchestraRoot)
    print(relpath(written, root))
    return 0


def cmd_refine(args, root: Path) -> int:
    config = load_config(root)
    feature = _resolve_feature_arg(args, root, config)
    doc = generate_epic_document(root, feature, config, agent=args.agent)
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
    ensure_git_info_exclude(root, config.project.orchestraRoot)
    print(relpath(output, root))
    report = validate_feature(root, feature, config)
    if not report.ok:
        for error in report.errors:
            print(f"error: {error}", file=sys.stderr)
        return 2
    return 0


def cmd_validate(args, root: Path) -> int:
    config = load_config(root)
    feature = _resolve_feature_arg(args, root, config)
    report = validate_feature(root, feature, config)
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
    ensure_git_info_exclude(root, config.project.orchestraRoot)
    feature = _resolve_feature_arg(args, root, config)
    only = args.only or args.epic
    return run_feature(
        root,
        feature,
        config,
        RunOptions(
            only=only,
            from_epic=args.from_epic,
            dry_run=args.dry_run,
            allow_dirty=args.allow_dirty,
            no_tests=args.no_tests,
            global_validation=args.global_validation,
            max_retries=args.max_retries,
            validation_retries=args.validation_retries,
            validation_timeout_ms=args.validation_timeout_ms,
            commit_mode=args.commit,
            agent=args.agent,
            mode=args.mode,
            continue_on_blocker=args.continue_on_blocker,
            force_unlock=args.force_unlock,
        ),
    )


def cmd_resume(args, root: Path) -> int:
    config = load_config(root)
    ensure_git_info_exclude(root, config.project.orchestraRoot)
    feature = _resolve_feature_arg(args, root, config)
    return run_feature(
        root,
        feature,
        config,
        RunOptions(
            allow_dirty=args.allow_dirty,
            no_tests=args.no_tests,
            max_retries=args.max_retries,
            validation_retries=args.validation_retries,
            validation_timeout_ms=args.validation_timeout_ms,
            commit_mode=args.commit,
            force_unlock=args.force_unlock,
            resume=True,
        ),
    )


def cmd_status(args, root: Path) -> int:
    config = load_config(root)
    feature = _resolve_feature_arg(args, root, config)
    artifacts = load_feature_artifacts(root, feature)
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
    feature = _resolve_feature_arg(args, root, config)
    artifacts = load_feature_artifacts(root, feature)
    from .epics import load_epics

    doc = load_epics(epics_path(root, config, artifacts.id))
    feature_dir = feature_state_dir(root, config, artifacts.id)
    state = load_state(feature_dir, doc.feature.path, config, doc)
    path = write_summary_report(feature_dir, doc, state)
    print(render_summary_report(doc, state))
    print(f"Report written to {relpath(path, root)}")
    return 0


def cmd_migrate(args, root: Path) -> int:
    result = migrate_project(root, config_dir=args.config_dir, dry_run=args.dry_run, backup=not args.no_backup)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if not result.ok:
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        return 2

    changed = [item for item in result.files if item.changed]
    action = "would update" if args.dry_run else "updated"
    for item in changed:
        print(f"{action}: {relpath(item.path, root)}")
        for detail in item.details:
            print(f"  - {detail}")
        if item.backup is not None:
            print(f"  backup: {relpath(item.backup, root)}")

    if not changed:
        print("Project artifacts are already up to date.")
    elif args.dry_run:
        print("Dry run complete; no files were changed.")
    else:
        print("Migration complete.")
    return 0


def cmd_clean(args, root: Path) -> int:
    if not args.dry_run and not args.yes:
        if not _terminal_interactive():
            print("error: clean requires --yes when stdin is not interactive", file=sys.stderr)
            return 2
        target = "runtime artifacts" if args.runtime_only else f"{args.config_dir} and local git excludes"
        if not _confirm(f"Remove {target}", default=False):
            print("Clean cancelled.")
            return 0

    result = clean_project(root, config_dir=args.config_dir, dry_run=args.dry_run, runtime_only=args.runtime_only)
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    if not result.ok:
        return 2

    paths = result.would_remove if args.dry_run else result.removed
    action = "would remove" if args.dry_run else "removed"
    for path in paths:
        print(f"{action}: {relpath(path, root)}")
    if result.updated_exclude:
        print("updated: .git/info/exclude")
    if not paths and not result.updated_exclude:
        print("No speckit-orchestra project artifacts found.")
    return 0


def cmd_doctor(args, root: Path) -> int:
    config = load_config(root, args.config_dir)
    if args.agent:
        config.agent.adapter = args.agent
        config.agent.command = args.agent
    adapter = get_adapter(config.agent.adapter)
    if adapter is None:
        print(f"error: unknown adapter {config.agent.adapter}", file=sys.stderr)
        return 2
    checks = _version_doctor_checks(root, config, args.config_dir) + _generic_doctor_checks(root) + adapter.doctor(
        config, root, smoke=not args.skip_smoke
    )
    ok = True
    for check in checks:
        marker = str(check.get("level") or ("ok" if check["ok"] else "fail"))
        print(f"[{marker}] {check['name']}: {check['detail']}")
        ok = ok and bool(check["ok"])
    return 0 if ok else 3


def cmd_adapters(args, root: Path) -> int:
    for name, adapter in ADAPTERS.items():
        print(f"{name}\tmode={adapter.mode}")
    return 0


def cmd_version(args) -> int:
    print(f"speckit-orchestra {_installed_version()}")
    return 0


def cmd_update(args) -> int:
    before = _installed_version()
    command = [sys.executable, "-m", "pip", "install", "--upgrade", PACKAGE_NAME]
    print(f"speckit-orchestra current version: {before}")
    print(f"updating with: {shlex.join(command)}")
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode != 0:
        _print_subprocess_output(result)
        print("error: update failed", file=sys.stderr)
        return result.returncode or 1
    after = _installed_version_from_subprocess() or _installed_version()
    print(f"speckit-orchestra installed version: {after}")
    if after == before:
        print("Already up to date.")
    else:
        print(f"Updated from {before} to {after}.")
    return 0


def _installed_version() -> str:
    try:
        return importlib_metadata.version(PACKAGE_NAME)
    except importlib_metadata.PackageNotFoundError:
        return __version__


def _installed_version_from_subprocess() -> str | None:
    code = "from importlib import metadata; print(metadata.version('speckit-orchestra'))"
    result = subprocess.run([sys.executable, "-c", code], text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _print_subprocess_output(result: subprocess.CompletedProcess[str]) -> None:
    if result.stdout.strip():
        print(result.stdout.strip())
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)


def _version_doctor_checks(root: Path, config, config_dir: str, *, include_path: bool = True) -> list[dict[str, object]]:
    current_version = _installed_version()
    checks: list[dict[str, object]] = [
        {
            "name": "speckit-orchestra version",
            "ok": True,
            "detail": f"{current_version}; executable {Path(sys.argv[0]).resolve()}; Python {sys.executable}",
        }
    ]
    checks.extend(_project_version_checks(root, config, config_dir, current_version))
    if include_path:
        checks.extend(_path_version_checks(current_version))
    return checks


def _project_version_checks(root: Path, config, config_dir: str, current_version: str) -> list[dict[str, object]]:
    path = config_path(root, config_dir)
    if not path.exists():
        return [
            {
                "name": "Project config",
                "ok": True,
                "level": "warn",
                "detail": f"not initialized; missing {relpath(path, root)}",
            }
        ]

    checks: list[dict[str, object]] = []
    if config.version > CURRENT_CONFIG_VERSION:
        checks.append(
            {
                "name": "Project config schema",
                "ok": False,
                "detail": f"schema {config.version} is newer than supported schema {CURRENT_CONFIG_VERSION}",
            }
        )
    elif config.version < CURRENT_CONFIG_VERSION:
        checks.append(
            {
                "name": "Project config schema",
                "ok": True,
                "level": "warn",
                "detail": f"schema {config.version}; current schema {CURRENT_CONFIG_VERSION}; run `sko migrate`",
            }
        )
    else:
        checks.append(
            {
                "name": "Project config schema",
                "ok": True,
                "detail": f"schema {config.version}; current schema {CURRENT_CONFIG_VERSION}",
            }
        )

    initialized = config.tool.versionInitialized
    migrated = config.tool.versionMigrated
    migrated_at = config.tool.lastMigratedAt
    detail = _project_metadata_detail(initialized, migrated, migrated_at)
    if not migrated:
        checks.append(
            {
                "name": "Project CLI metadata",
                "ok": True,
                "level": "warn",
                "detail": f"{detail}; run `sko migrate` to record local CLI metadata",
            }
        )
    elif _is_newer_version(current_version, migrated):
        checks.append(
            {
                "name": "Project CLI metadata",
                "ok": True,
                "level": "warn",
                "detail": f"{detail}; current CLI is {current_version}; run `sko migrate`",
            }
        )
    else:
        checks.append({"name": "Project CLI metadata", "ok": True, "detail": detail})
    return checks


def _project_metadata_detail(initialized: str | None, migrated: str | None, migrated_at: str | None) -> str:
    parts = [
        f"initialized {initialized}" if initialized else "initialized version not recorded",
        f"last migrated {migrated}" if migrated else "migration version not recorded",
    ]
    if migrated_at:
        parts.append(f"at {migrated_at}")
    return "; ".join(parts)


def _path_version_checks(current_version: str) -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    seen: set[str] = set()
    for command in ("sko", "speckit-orchestra", "orchestra"):
        path = shutil.which(command)
        if path is None or path in seen:
            continue
        seen.add(path)
        version = _version_from_executable(path)
        if version is None:
            checks.append(
                {
                    "name": f"PATH {command} version",
                    "ok": True,
                    "level": "warn",
                    "detail": f"could not read version from {path}",
                }
            )
        elif version != current_version:
            checks.append(
                {
                    "name": f"PATH {command} version",
                    "ok": True,
                    "level": "warn",
                    "detail": f"{version} at {path}; current CLI is {current_version}",
                }
            )
        else:
            checks.append({"name": f"PATH {command} version", "ok": True, "detail": f"{version} at {path}"})
    if not checks:
        checks.append(
            {
                "name": "PATH speckit-orchestra scripts",
                "ok": True,
                "level": "info",
                "detail": "no sko, speckit-orchestra, or orchestra executable found on PATH",
            }
        )
    return checks


def _version_from_executable(path: str) -> str | None:
    try:
        result = subprocess.run(
            [path, "--version"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    return _parse_version_output(result.stdout)


def _parse_version_output(output: str) -> str | None:
    parts = output.strip().split()
    if not parts:
        return None
    return parts[-1]


def _is_newer_version(version: str, other: str) -> bool:
    version_key = _version_key(version)
    other_key = _version_key(other)
    if version_key is None or other_key is None:
        return False
    return version_key > other_key


def _version_key(version: str) -> tuple[int, int, int] | None:
    parts = version.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return int(parts[0]), int(parts[1]), int(parts[2])


def _resolve_feature_arg(args, root: Path, config) -> str:
    feature = getattr(args, "feature", None)
    if feature:
        return _resolve_feature_reference(root, config, feature)
    return _select_feature(root, config)


def _resolve_feature_reference(root: Path, config, feature: str) -> str:
    path = Path(feature)
    if path.is_absolute() or (root / path).exists():
        return feature
    spec_candidate = root / config.project.specRoot / feature
    if spec_candidate.exists():
        return relpath(spec_candidate, root)
    return feature


def _select_feature(root: Path, config) -> str:
    features = discover_feature_paths(root, config.project.specRoot)
    if not features:
        raise ValueError(f"no Spec Kit features found under {config.project.specRoot}")
    if not _terminal_interactive():
        raise ValueError("feature argument is required when stdin is not interactive")
    choices = [relpath(path, root) for path in features]
    selected = _choose("Feature", choices)
    if not selected:
        raise ValueError("no feature selected")
    return selected


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


def _should_discover(value: bool | None, *, default: bool, config) -> bool:
    if config.agent.adapter != "opencode":
        return False
    enabled = default if value is None else value
    if enabled and not _terminal_interactive():
        print("warning: opencode discovery requires an interactive terminal; skipping menu", file=sys.stderr)
        return False
    return enabled


def _configure_opencode_interactive(config, root: Path) -> None:
    print("\nopencode adapter setup")
    discovery = discover_opencode(root, config.agent.command)
    if not discovery.available:
        print(f"Could not discover opencode: {'; '.join(discovery.errors)}")
        _free_text_opencode_config(config)
        return
    for error in discovery.errors:
        print(f"warning: {error}")

    provider = _choose("Provider", _provider_choices(discovery), current=config.agent.provider)
    if provider:
        config.agent.provider = provider

    model_choices = _model_choices(discovery, config.agent.provider)
    model = _choose("Model", model_choices, current=_current_model(config), allow_custom=True)
    if model:
        _set_model(config, model)

    variant = _choose(
        "Reasoning / variant",
        ["minimal", "low", "medium", "high", "max"],
        current=config.agent.variant,
        allow_custom=True,
    )
    if variant:
        config.agent.variant = variant

    agent = _choose("opencode agent", discovery.agents, current=config.agent.opencodeAgent, allow_custom=True)
    if agent:
        config.agent.opencodeAgent = agent

    config.agent.thinking = _confirm("Show thinking blocks", default=config.agent.thinking)


def _free_text_opencode_config(config) -> None:
    provider = _prompt_text("Provider ID", config.agent.provider)
    model = _prompt_text("Model", _current_model(config))
    variant = _prompt_text("Reasoning / variant", config.agent.variant)
    agent = _prompt_text("opencode agent", config.agent.opencodeAgent)
    if provider:
        config.agent.provider = provider
    if model:
        _set_model(config, model)
    if variant:
        config.agent.variant = variant
    if agent:
        config.agent.opencodeAgent = agent
    config.agent.thinking = _confirm("Show thinking blocks", default=config.agent.thinking)


def _provider_choices(discovery: OpencodeDiscovery) -> list[str]:
    return sorted(discovery.providers)


def _model_choices(discovery: OpencodeDiscovery, provider: str | None) -> list[str]:
    if provider:
        models = [model for model in discovery.models if model.startswith(f"{provider}/")]
        if models:
            return models
    return discovery.models


def _choose(label: str, choices: list[str], *, current: str | None = None, allow_custom: bool = False) -> str | None:
    if not choices:
        return _prompt_text(label, current) if allow_custom else current
    if _terminal_interactive():
        selected = _arrow_choose(label, choices, current=current, allow_custom=allow_custom)
        if selected is _CUSTOM_SELECTION:
            return _prompt_text(label, current)
        return selected
    return _numbered_choose(label, choices, current=current, allow_custom=allow_custom)


def _numbered_choose(label: str, choices: list[str], *, current: str | None = None, allow_custom: bool = False) -> str | None:
    title = f"{ANSI_BOLD}{label}{ANSI_RESET}" if sys.stdout.isatty() else label
    print(f"\n{title}")
    for index, choice in enumerate(choices, start=1):
        marker = " [current]" if choice == current else ""
        print(f"  {index:>2}. {choice}{marker}")
    custom = " or type a custom value" if allow_custom else ""
    prompt = f"Choose {label.lower()} [Enter to keep"
    prompt += f" {current}" if current else " blank"
    prompt += f", number{custom}]: "
    value = input(prompt).strip()
    if not value:
        return current
    if value.isdigit():
        index = int(value)
        if 1 <= index <= len(choices):
            return choices[index - 1]
        print("Invalid selection; keeping current value.")
        return current
    if allow_custom:
        return value
    print("Invalid selection; keeping current value.")
    return current


def _arrow_choose(
    label: str,
    choices: list[str],
    *,
    current: str | None = None,
    allow_custom: bool = False,
) -> str | None | object:
    try:
        import termios
        import tty
    except ImportError:
        return _numbered_choose(label, choices, current=current, allow_custom=allow_custom)

    selected = choices.index(current) if current in choices else 0
    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        return _numbered_choose(label, choices, current=current, allow_custom=allow_custom)
    rendered_lines = 0
    sys.stdout.write("\x1b[?25l")
    try:
        tty.setcbreak(fd)
        while True:
            lines = _menu_lines(label, choices, selected, current=current, allow_custom=allow_custom)
            if rendered_lines:
                sys.stdout.write(f"\x1b[{rendered_lines}A")
            for line in lines:
                sys.stdout.write("\x1b[2K\r" + line + "\n")
            sys.stdout.flush()
            rendered_lines = len(lines)

            key = sys.stdin.read(1)
            if key == "\x03":
                raise KeyboardInterrupt
            if key in {"\r", "\n"}:
                return choices[selected]
            if key == "\x1b":
                next_key = _read_key_if_ready()
                if next_key == "[":
                    direction = _read_key_if_ready()
                    if direction == "A":
                        selected = (selected - 1) % len(choices)
                    elif direction == "B":
                        selected = (selected + 1) % len(choices)
                    continue
                return current
            if key.lower() == "k":
                selected = (selected - 1) % len(choices)
            elif key.lower() == "j":
                selected = (selected + 1) % len(choices)
            elif key.lower() == "q":
                return current
            elif allow_custom and key.lower() == "c":
                return _CUSTOM_SELECTION
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        sys.stdout.write("\x1b[?25h")
        sys.stdout.flush()


def _menu_lines(
    label: str,
    choices: list[str],
    selected: int,
    *,
    current: str | None = None,
    allow_custom: bool = False,
) -> list[str]:
    lines = [f"{ANSI_BOLD}{label}{ANSI_RESET}"]
    for index, choice in enumerate(choices):
        prefix = ">" if index == selected else " "
        marker = " [current]" if choice == current else ""
        text = f"{prefix} {choice}{marker}"
        if index == selected:
            text = f"{ANSI_REVERSE}{text}{ANSI_RESET}"
        lines.append(text)
    keep = "keep current" if current else "cancel"
    footer = f"Use Up/Down or j/k, Enter to select, q/Esc to {keep}"
    if allow_custom:
        footer += ", c for custom"
    lines.append(f"{ANSI_DIM}{footer}.{ANSI_RESET}")
    return lines


def _read_key_if_ready(timeout: float = 0.05) -> str:
    import select

    ready, _, _ = select.select([sys.stdin], [], [], timeout)
    if not ready:
        return ""
    return sys.stdin.read(1)


def _terminal_interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def _prompt_text(label: str, current: str | None = None) -> str | None:
    suffix = f" [{current}]" if current else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or current


def _confirm(label: str, *, default: bool = False) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = input(f"{label}? [{suffix}] ").strip().lower()
    if not value:
        return default
    return value in {"y", "yes"}


def _set_model(config, model: str | None) -> None:
    if not model:
        return
    if "/" in model:
        provider, model_name = model.split("/", 1)
        config.agent.provider = provider
        config.agent.model = model_name
    else:
        config.agent.model = model


def _current_model(config) -> str | None:
    if not config.agent.model:
        return None
    if "/" in config.agent.model or not config.agent.provider:
        return config.agent.model
    return f"{config.agent.provider}/{config.agent.model}"
