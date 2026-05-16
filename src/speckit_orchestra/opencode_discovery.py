from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
AGENT_RE = re.compile(r"^([A-Za-z0-9_.-]+)\s+\((primary|subagent)\)")
PROVIDER_LABEL_RE = re.compile(r"^[●○]\s+(.+?)(?:\s{2,}|\s+(?:oauth|api|key|local)\b|$)")


@dataclass(frozen=True)
class OpencodeDiscovery:
    available: bool
    command: str
    providers: list[str] = field(default_factory=list)
    provider_labels: dict[str, str] = field(default_factory=dict)
    models: list[str] = field(default_factory=list)
    agents: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def discover_opencode(root: Path, command: str = "opencode") -> OpencodeDiscovery:
    command_path = shutil.which(command)
    if command_path is None:
        return OpencodeDiscovery(False, command, errors=[f"{command!r} was not found on PATH"])

    errors: list[str] = []
    models_output, model_error = _run(command_path, ["models"], root)
    if model_error:
        errors.append(model_error)
    models = parse_models(models_output)

    providers_output, provider_error = _run(command_path, ["providers", "list"], root)
    if provider_error:
        errors.append(provider_error)
    provider_labels = parse_provider_labels(providers_output)
    providers = sorted({model.split("/", 1)[0] for model in models} | set(provider_labels))

    agents_output, agent_error = _run(command_path, ["agent", "list"], root)
    if agent_error:
        errors.append(agent_error)
    agents = parse_agents(agents_output)

    return OpencodeDiscovery(True, command, providers=providers, provider_labels=provider_labels, models=models, agents=agents, errors=errors)


def parse_models(output: str) -> list[str]:
    models: list[str] = []
    for line in _clean_lines(output):
        if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.:-]+$", line):
            models.append(line)
    return sorted(dict.fromkeys(models))


def parse_agents(output: str) -> list[str]:
    agents: list[str] = []
    for line in _clean_lines(output):
        match = AGENT_RE.match(line)
        if match:
            agents.append(match.group(1))
    return sorted(dict.fromkeys(agents))


def parse_provider_labels(output: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for line in _clean_lines(output):
        match = PROVIDER_LABEL_RE.match(line)
        if not match:
            continue
        label = match.group(1).strip()
        provider_id = _slug(label)
        labels[provider_id] = label
    return labels


def _run(command: str, args: list[str], root: Path) -> tuple[str, str | None]:
    try:
        result = subprocess.run(
            [command, *args],
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "", f"opencode {' '.join(args)} timed out"
    except OSError as exc:
        return "", f"opencode {' '.join(args)} failed to start: {exc}"
    if result.returncode != 0:
        return result.stdout, (result.stderr or result.stdout).strip() or f"opencode {' '.join(args)} failed"
    return result.stdout, None


def _clean_lines(output: str) -> list[str]:
    lines: list[str] = []
    for raw in output.splitlines():
        line = ANSI_RE.sub("", raw).strip()
        if line:
            lines.append(line)
    return lines


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
