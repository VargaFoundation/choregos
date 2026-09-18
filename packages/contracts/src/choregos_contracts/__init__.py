"""Contrats Choregos : schémas JSON, OpenAPI et types Python.

`packages/contracts` est la **source de vérité** des interfaces entre les flux.
Toute modification passe par une PR taguée `contract-change` (docs/plan/01).
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from .enums import (
    ActorType,
    ApiFormat,
    ConnectorKind,
    EventType,
    ExecutorKind,
    FindingStatus,
    FindingType,
    HumanRequestKind,
    InboundEventType,
    MemoryKind,
    ProjectStatus,
    ReleaseStatus,
    Risk,
    Role,
    RunStatus,
    Severity,
    Size,
    StageRole,
    StageStatus,
    StateKind,
)
from .events import ChoregosEvent, Control, DeployResult, HumanDecision, InboundEvent
from .policy import (
    ApprovalRule,
    Approvals,
    Attempts,
    Budgets,
    FindingsPolicy,
    MemoryPolicy,
    Policy,
    PolicyMetadata,
    ReviewPolicy,
    SandboxPolicy,
    ScopePolicy,
    TrainEnvPolicy,
)
from .project import AgentConfig, ModelProfile, ModelsConfig, ProjectConfig, RepoConfig
from .stage import (
    STAGE_INPUT_SCHEMA,
    STAGE_RESULT_SCHEMA,
    AgentRef,
    Artifacts,
    Budget,
    Callbacks,
    ContextPack,
    Diagnostics,
    Evidence,
    Finding,
    LaunchSpec,
    McpServerRef,
    MemoryItem,
    ModelRef,
    Permissions,
    PlaybookRef,
    ProjectRef,
    Question,
    RelatedItem,
    RepoRef,
    ScopeChangeRequest,
    StageInput,
    StageOutputs,
    StageResult,
    ToolsRef,
    TransitionRef,
    WorkItemLinks,
    WorkItemRef,
)
from .workflow import (
    AgentActor,
    GateSpec,
    HumanActor,
    Retry,
    State,
    SystemActor,
    Transition,
    Workflow,
    WorkflowDefaults,
    WorkflowMetadata,
)

__version__ = "0.1.0"

SCHEMA_FILES = (
    "workflow.schema.json",
    "policy.schema.json",
    "project.schema.json",
    "stage-input.schema.json",
    "stage-result.schema.json",
    "finding.schema.json",
    "inbound-event.schema.json",
    "human-decision.schema.json",
    "context-pack.schema.json",
    "template.schema.json",
    "event.schema.json",
)


def contracts_dir() -> Path:
    """Racine des contrats : `packages/contracts` en développement, le paquet installé sinon."""
    root = Path(str(resources.files("choregos_contracts"))).resolve()
    if (root / "schemas").is_dir():  # wheel : les schémas sont embarqués
        return root
    return root.parent.parent  # src/choregos_contracts -> packages/contracts


def schemas_dir() -> Path:
    return contracts_dir() / "schemas"


def load_schema(name: str) -> dict[str, Any]:
    """Charge un JSON Schema par nom de fichier (ex. `workflow.schema.json`)."""
    path = schemas_dir() / name
    if not path.exists():
        raise FileNotFoundError(f"schéma introuvable : {path}")
    with path.open(encoding="utf-8") as fh:
        data: dict[str, Any] = json.load(fh)
    return data


def openapi_path() -> Path:
    return contracts_dir() / "openapi.yaml"


def load_openapi() -> dict[str, Any]:
    with openapi_path().open(encoding="utf-8") as fh:
        data: dict[str, Any] = yaml.safe_load(fh)
    return data


__all__ = [
    "STAGE_INPUT_SCHEMA",
    "STAGE_RESULT_SCHEMA",
    "ActorType",
    "AgentActor",
    "AgentConfig",
    "AgentRef",
    "ApiFormat",
    "ApprovalRule",
    "Approvals",
    "Artifacts",
    "Attempts",
    "Budget",
    "Budgets",
    "Callbacks",
    "ChoregosEvent",
    "ConnectorKind",
    "ContextPack",
    "Control",
    "DeployResult",
    "Diagnostics",
    "EventType",
    "Evidence",
    "ExecutorKind",
    "Finding",
    "FindingStatus",
    "FindingType",
    "FindingsPolicy",
    "GateSpec",
    "HumanActor",
    "HumanDecision",
    "HumanRequestKind",
    "InboundEvent",
    "InboundEventType",
    "LaunchSpec",
    "McpServerRef",
    "MemoryItem",
    "MemoryKind",
    "MemoryPolicy",
    "ModelProfile",
    "ModelRef",
    "ModelsConfig",
    "Permissions",
    "PlaybookRef",
    "Policy",
    "PolicyMetadata",
    "ProjectConfig",
    "ProjectRef",
    "ProjectStatus",
    "Question",
    "RelatedItem",
    "ReleaseStatus",
    "RepoConfig",
    "RepoRef",
    "Retry",
    "ReviewPolicy",
    "Risk",
    "Role",
    "RunStatus",
    "SandboxPolicy",
    "ScopeChangeRequest",
    "ScopePolicy",
    "Severity",
    "Size",
    "StageInput",
    "StageOutputs",
    "StageResult",
    "StageRole",
    "StageStatus",
    "State",
    "StateKind",
    "SystemActor",
    "ToolsRef",
    "TrainEnvPolicy",
    "Transition",
    "TransitionRef",
    "WorkItemLinks",
    "WorkItemRef",
    "Workflow",
    "WorkflowDefaults",
    "WorkflowMetadata",
    "contracts_dir",
    "load_openapi",
    "load_schema",
    "openapi_path",
    "schemas_dir",
]
