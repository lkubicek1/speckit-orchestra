from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from . import __version__
from .utils import read_yaml, write_yaml


CONFIG_VERSION = 3


DEFAULT_COMMIT_TEMPLATE = """feat({featureId}): implement {epicId} {epicTitle}

Spec: {specPath}
Epic: {epicId}
Tasks: {taskIds}

Validation:
{validationSummary}

Generated-by: speckit-orchestra
"""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProjectConfig(StrictModel):
    name: str
    specRoot: str = "specs"
    orchestraRoot: str = ".spec-orchestra"


class AgentConfig(StrictModel):
    adapter: str = "opencode"
    mode: str = "cli"
    command: str = "opencode"
    args: list[str] = Field(default_factory=lambda: ["run"])
    provider: str | None = None
    model: str | None = None
    variant: str | None = None
    opencodeAgent: str | None = None
    thinking: bool = False
    promptInput: str = "stdin"
    outputFormat: str = "text"
    timeoutMs: int = 1_800_000


class AutomationConfig(StrictModel):
    refineWithAgent: str = "opencode"


class ExecutionConfig(StrictModel):
    mode: Literal["sequential"] = "sequential"
    maxRetries: int = 1
    validationRetries: int = 5
    continueOnBlocker: bool = False
    requireCleanGit: bool = True
    useWorktrees: bool = False
    haltOnBlocker: bool = True
    haltWhenComplete: bool = True


class CommitConfig(StrictModel):
    mode: Literal["auto", "ask", "never"] = "ask"
    messageTemplate: str = DEFAULT_COMMIT_TEMPLATE


class ValidationConfig(StrictModel):
    globalCommands: list[str] = Field(default_factory=list)
    blockOnForbiddenPaths: bool = True
    blockOnUntrackedFiles: bool = False


class LoggingConfig(StrictModel):
    preserveStdout: bool = True
    preserveStderr: bool = True
    preserveDiffs: bool = True


class ToolConfig(StrictModel):
    versionInitialized: str | None = None
    versionMigrated: str | None = None
    lastMigratedAt: str | None = None


class Config(StrictModel):
    version: int = CONFIG_VERSION
    project: ProjectConfig
    agent: AgentConfig = Field(default_factory=AgentConfig)
    automation: AutomationConfig = Field(default_factory=AutomationConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    commit: CommitConfig = Field(default_factory=CommitConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    tool: ToolConfig = Field(default_factory=ToolConfig)


def default_config(
    root: Path,
    *,
    agent: str = "opencode",
    mode: str = "cli",
    config_dir: str = ".spec-orchestra",
    commit_mode: str = "ask",
    provider: str | None = None,
    model: str | None = None,
    variant: str | None = None,
    opencode_agent: str | None = None,
    thinking: bool = False,
) -> Config:
    command = agent
    args = ["run"] if agent == "opencode" else []
    return Config(
        project=ProjectConfig(name=root.name, orchestraRoot=config_dir),
        agent=AgentConfig(
            adapter=agent,
            mode=mode,
            command=command,
            args=args,
            provider=provider,
            model=model,
            variant=variant,
            opencodeAgent=opencode_agent,
            thinking=thinking,
        ),
        automation=AutomationConfig(refineWithAgent=agent),
        commit=CommitConfig(mode=commit_mode),
        tool=ToolConfig(versionInitialized=__version__, versionMigrated=__version__),
    )


def config_path(root: Path, config_dir: str = ".spec-orchestra") -> Path:
    return root / config_dir / "config.yaml"


def load_config(root: Path, config_dir: str = ".spec-orchestra") -> Config:
    path = config_path(root, config_dir)
    if not path.exists():
        return default_config(root, config_dir=config_dir)
    return Config.model_validate(read_yaml(path))


def write_config(root: Path, config: Config) -> Path:
    path = root / config.project.orchestraRoot / "config.yaml"
    write_yaml(path, config.model_dump(mode="json"))
    return path
