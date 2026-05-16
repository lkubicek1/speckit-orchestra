from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import Config


@dataclass(frozen=True)
class AgentInvocation:
    command: str
    args: list[str]
    cwd: Path
    stdin: str
    timeout_ms: int


@dataclass(frozen=True)
class AgentRunResult:
    status: str
    exit_code: int | None
    summary: str
    blocker: dict[str, Any] | None = None


class AgentHarness:
    name = "base"
    mode = "cli"

    def doctor(self, config: Config, root: Path, *, smoke: bool = True) -> list[dict[str, Any]]:
        raise NotImplementedError

    def build_invocation(self, config: Config, root: Path, prompt: str) -> AgentInvocation:
        raise NotImplementedError

    def run(self, invocation: AgentInvocation, stdout_path: Path, stderr_path: Path) -> AgentRunResult:
        raise NotImplementedError


class OpencodeCliAdapter(AgentHarness):
    name = "opencode"
    mode = "cli"

    def doctor(self, config: Config, root: Path, *, smoke: bool = True) -> list[dict[str, Any]]:
        command = config.agent.command or "opencode"
        checks: list[dict[str, Any]] = []
        command_path = shutil.which(command)
        checks.append(
            {
                "name": "opencode command",
                "ok": command_path is not None,
                "detail": command_path or f"{command!r} was not found on PATH",
            }
        )
        if not command_path:
            return checks

        try:
            version = subprocess.run(
                [command_path, "--version"],
                cwd=root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=15,
            )
        except OSError as exc:
            checks.append(
                {
                    "name": "opencode version",
                    "ok": False,
                    "detail": f"failed to start {command_path}: {exc}",
                }
            )
            return checks
        checks.append(
            {
                "name": "opencode version",
                "ok": version.returncode == 0,
                "detail": (version.stdout or version.stderr).strip() or "version unavailable",
            }
        )

        if smoke:
            prompt = "Print exactly: SPECKIT_ORCHESTRA_READY\n"
            invocation = self.build_invocation(config, root, prompt)
            try:
                result = subprocess.run(
                    [_resolve_command(invocation.command), *invocation.args],
                    input=invocation.stdin,
                    cwd=invocation.cwd,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    timeout=60,
                )
                output = f"{result.stdout}\n{result.stderr}"
                checks.append(
                    {
                        "name": "opencode non-interactive smoke test",
                        "ok": result.returncode == 0 and "SPECKIT_ORCHESTRA_READY" in output,
                        "detail": output.strip()[-500:] or f"exit code {result.returncode}",
                    }
                )
            except subprocess.TimeoutExpired:
                checks.append(
                    {
                        "name": "opencode non-interactive smoke test",
                        "ok": False,
                        "detail": "timed out after 60 seconds",
                    }
                )
        return checks

    def build_invocation(self, config: Config, root: Path, prompt: str) -> AgentInvocation:
        return AgentInvocation(
            command=config.agent.command,
            args=build_opencode_args(config),
            cwd=root,
            stdin=prompt,
            timeout_ms=config.agent.timeoutMs,
        )

    def run(self, invocation: AgentInvocation, stdout_path: Path, stderr_path: Path) -> AgentRunResult:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            result = subprocess.run(
                [_resolve_command(invocation.command), *invocation.args],
                input=invocation.stdin,
                cwd=invocation.cwd,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=invocation.timeout_ms / 1000,
            )
        except FileNotFoundError:
            stderr_path.write_text(f"command not found: {invocation.command}\n", encoding="utf-8")
            stdout_path.write_text("", encoding="utf-8")
            return AgentRunResult(
                status="failed",
                exit_code=None,
                summary="adapter command not found",
                blocker={
                    "category": "agent_error",
                    "message": f"Command not found: {invocation.command}",
                    "suggestedNextAction": "Install the adapter CLI or update .spec-orchestra/config.yaml.",
                },
            )
        except subprocess.TimeoutExpired as exc:
            stdout_path.write_text(exc.stdout or "", encoding="utf-8")
            stderr_path.write_text((exc.stderr or "") + "\nTimed out.\n", encoding="utf-8")
            return AgentRunResult(
                status="failed",
                exit_code=None,
                summary="adapter timed out",
                blocker={
                    "category": "agent_error",
                    "message": f"Adapter timed out after {invocation.timeout_ms}ms.",
                    "suggestedNextAction": "Increase agent.timeoutMs or run the epic manually.",
                },
            )

        stdout_path.write_text(result.stdout, encoding="utf-8")
        stderr_path.write_text(result.stderr, encoding="utf-8")
        if result.returncode == 0:
            return AgentRunResult(status="complete", exit_code=0, summary="adapter exited successfully")
        return AgentRunResult(
            status="failed",
            exit_code=result.returncode,
            summary=f"adapter exited with code {result.returncode}",
            blocker={
                "category": "agent_error",
                "message": f"Adapter exited with code {result.returncode}.",
                "suggestedNextAction": "Inspect stdout.log and stderr.log, then resume after fixing the issue.",
            },
        )


ADAPTERS: dict[str, AgentHarness] = {"opencode": OpencodeCliAdapter()}


def _resolve_command(command: str) -> str:
    return shutil.which(command) or command


def get_adapter(name: str) -> AgentHarness | None:
    return ADAPTERS.get(name)


def build_opencode_args(config: Config) -> list[str]:
    args = list(config.agent.args or ["run"])
    if not args:
        args.append("run")
    model = _qualified_model(config.agent.provider, config.agent.model)
    _append_flag(args, ["--model", "-m"], "--model", model)
    _append_flag(args, ["--variant"], "--variant", config.agent.variant)
    _append_flag(args, ["--agent"], "--agent", config.agent.opencodeAgent)
    if config.agent.thinking and "--thinking" not in args:
        args.append("--thinking")
    return args


def _qualified_model(provider: str | None, model: str | None) -> str | None:
    if not model:
        return None
    if "/" in model or not provider:
        return model
    return f"{provider}/{model}"


def _append_flag(args: list[str], existing_flags: list[str], flag: str, value: str | None) -> None:
    if not value or any(existing in args for existing in existing_flags):
        return
    args.extend([flag, value])
