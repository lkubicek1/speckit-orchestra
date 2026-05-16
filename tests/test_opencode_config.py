from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from speckit_orchestra.adapters import build_opencode_args
from speckit_orchestra.config import default_config
from speckit_orchestra.opencode_discovery import discover_opencode, parse_agents, parse_models, parse_provider_labels


def test_build_opencode_args_from_first_class_config(tmp_path: Path) -> None:
    config = default_config(
        tmp_path,
        provider="openai",
        model="gpt-5.5",
        variant="high",
        opencode_agent="build",
        thinking=True,
    )

    assert build_opencode_args(config) == [
        "run",
        "--model",
        "openai/gpt-5.5",
        "--variant",
        "high",
        "--agent",
        "build",
        "--thinking",
    ]


def test_build_opencode_args_does_not_duplicate_explicit_args(tmp_path: Path) -> None:
    config = default_config(tmp_path, provider="openai", model="gpt-5.5", variant="high")
    config.agent.args = ["run", "--model", "github-copilot/claude-sonnet-4.5", "--variant", "max"]

    assert build_opencode_args(config) == ["run", "--model", "github-copilot/claude-sonnet-4.5", "--variant", "max"]


def test_parse_opencode_models() -> None:
    output = """openai/gpt-5.5
github-copilot/claude-sonnet-4.5
not a model
"""

    assert parse_models(output) == ["github-copilot/claude-sonnet-4.5", "openai/gpt-5.5"]


def test_parse_opencode_agents() -> None:
    output = """build (primary)
  [
explore (subagent)
  [
"""

    assert parse_agents(output) == ["build", "explore"]


def test_parse_opencode_provider_labels() -> None:
    output = "\x1b[0m●  OpenAI \x1b[90moauth\n●  GitHub Copilot \x1b[90moauth\n"

    assert parse_provider_labels(output) == {"github-copilot": "GitHub Copilot", "openai": "OpenAI"}


def test_discover_opencode_runs_resolved_command_path(tmp_path: Path, monkeypatch) -> None:
    import speckit_orchestra.opencode_discovery as opencode_discovery

    resolved = "C:/Users/me/AppData/Roaming/npm/opencode.cmd"
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        command_args = args[1:]
        if command_args == ["models"]:
            stdout = "openai/gpt-5.5\n"
        elif command_args == ["providers", "list"]:
            stdout = "\x1b[0m●  OpenAI \x1b[90moauth\n"
        else:
            stdout = "build (primary)\n"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    monkeypatch.setattr(opencode_discovery.shutil, "which", lambda command: resolved if command == "opencode" else None)
    monkeypatch.setattr(opencode_discovery.subprocess, "run", fake_run)

    discovery = discover_opencode(tmp_path)

    assert discovery.available is True
    assert all(call[0] == resolved for call in calls)


def test_opencode_doctor_runs_resolved_command_path(tmp_path: Path, monkeypatch) -> None:
    import speckit_orchestra.adapters as adapters

    resolved = "C:/Users/me/AppData/Roaming/npm/opencode.cmd"
    calls: list[list[str]] = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0, stdout="opencode 1.0.0\n", stderr="")

    monkeypatch.setattr(adapters.shutil, "which", lambda command: resolved if command == "opencode" else None)
    monkeypatch.setattr(adapters.subprocess, "run", fake_run)

    checks = adapters.OpencodeCliAdapter().doctor(default_config(tmp_path), tmp_path, smoke=False)

    assert checks[1]["ok"] is True
    assert calls == [[resolved, "--version"]]
