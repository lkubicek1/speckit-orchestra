from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .utils import read_yaml, write_yaml


class EpicModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FeatureRef(EpicModel):
    id: str
    path: str
    spec: str
    plan: str
    tasks: str


class EpicExecution(EpicModel):
    recommendedMode: str = "sequential"
    recommendedAdapter: str = "opencode"


class Approval(EpicModel):
    required: bool = False
    reason: str | None = None


class Scope(EpicModel):
    include: list[str] = Field(min_length=1)
    exclude: list[str] = Field(default_factory=list)


class Validation(EpicModel):
    commands: list[str] = Field(default_factory=list)
    manualChecks: list[str] = Field(default_factory=list)
    expectedFailureAllowed: bool = False

    @model_validator(mode="after")
    def has_validation(self) -> "Validation":
        if not self.commands and not self.manualChecks:
            raise ValueError("validation.commands or validation.manualChecks is required")
        return self


class Epic(EpicModel):
    id: str
    title: str
    goal: str
    tasks: list[str] = Field(min_length=1)
    dependencies: list[str] = Field(default_factory=list)
    risk: str
    parallelSafe: bool = False
    approval: Approval = Field(default_factory=Approval)
    scope: Scope
    acceptance: list[str] = Field(min_length=1)
    validation: Validation
    stopConditions: list[str] = Field(min_length=1)


class ExcludedTask(EpicModel):
    id: str
    reason: str


class EpicDocument(EpicModel):
    version: int = 1
    feature: FeatureRef
    execution: EpicExecution = Field(default_factory=EpicExecution)
    epics: list[Epic] = Field(min_length=1)
    excludedTasks: list[ExcludedTask] = Field(default_factory=list)


def load_epics(path: Path) -> EpicDocument:
    try:
        return EpicDocument.model_validate(read_yaml(path))
    except ValidationError:
        raise
    except Exception as exc:
        raise ValueError(f"failed to load epics from {path}: {exc}") from exc


def write_epics(path: Path, doc: EpicDocument | dict[str, Any]) -> None:
    if isinstance(doc, EpicDocument):
        data = doc.model_dump(mode="json")
    else:
        data = doc
    write_yaml(path, data)
