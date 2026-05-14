from __future__ import annotations

from pathlib import Path

from speckit_orchestra.adapters import build_opencode_args
from speckit_orchestra.config import default_config
from speckit_orchestra.opencode_discovery import parse_agents, parse_models, parse_provider_labels


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
