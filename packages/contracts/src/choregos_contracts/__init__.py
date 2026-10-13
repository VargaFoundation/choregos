# SPDX-License-Identifier: Apache-2.0
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

from .artifact import (
    ARTIFACT_NAME_PATTERN,
    ARTIFACT_TYPES,
    Artifact,
    ArtifactKind,
    ArtifactProducer,
    ArtifactType,
    artifact_digest,
    artifact_type,
    inline_bytes,
    type_of_filename,
    type_of_value,
)
from .enums import (
    ActionOrigin,
    ActionStatus,
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
    ContextOverride,
    ContextPolicy,
    FindingsPolicy,
    MemoryPolicy,
    Policy,
    PolicyMetadata,
    ReviewPolicy,
    SandboxPolicy,
    ScopePolicy,
    TrainEnvPolicy,
)
from .project import AgentConfig, DodConfig, ModelProfile, ModelsConfig, ProjectConfig, RepoConfig
from .stage import (
    STAGE_INPUT_SCHEMA,
    STAGE_RESULT_SCHEMA,
    AgentRef,
    ArtifactFile,
    Artifacts,
    Budget,
    Callbacks,
    ContextPack,
    Diagnostics,
    Evidence,
    Finding,
    KnowledgeRef,
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
    SkillRef,
    StageContext,
    StageInput,
    StageOutputs,
    StageResult,
    ToolsRef,
    TransitionRef,
    WorkItemLinks,
    WorkItemRef,
    empreinte_de_skill,
    file_outputs,
    knowledge_digest,
    missing_outputs,
)
from .workflow import (
    ActionApprovalSpec,
    ActionApprover,
    ActionEffectSpec,
    AgentActor,
    GateSpec,
    HumanActor,
    Retry,
    State,
    SystemActor,
    TaskSpec,
    Transition,
    TransitionAction,
    Workflow,
    WorkflowBudget,
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
    "ui-manifest.schema.json",
    "artifact.schema.json",
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
        raise FileNotFoundError(f"schema not found: {path}")
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
    "ARTIFACT_NAME_PATTERN",
    "ARTIFACT_TYPES",
    "STAGE_INPUT_SCHEMA",
    "STAGE_RESULT_SCHEMA",
    "ActionApprovalSpec",
    "ActionApprover",
    "ActionEffectSpec",
    "ActionOrigin",
    "ActionStatus",
    "ActorType",
    "AgentActor",
    "AgentConfig",
    "AgentRef",
    "ApiFormat",
    "ApprovalRule",
    "Approvals",
    "Artifact",
    "ArtifactFile",
    "ArtifactKind",
    "ArtifactProducer",
    "ArtifactType",
    "Artifacts",
    "Attempts",
    "Budget",
    "Budgets",
    "Callbacks",
    "ChoregosEvent",
    "ConnectorKind",
    "ContextOverride",
    "ContextPack",
    "ContextPolicy",
    "Control",
    "DeployResult",
    "Diagnostics",
    "DodConfig",
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
    "KnowledgeRef",
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
    "SkillRef",
    "StageContext",
    "StageInput",
    "StageOutputs",
    "StageResult",
    "StageRole",
    "StageStatus",
    "State",
    "StateKind",
    "SystemActor",
    "TaskSpec",
    "ToolsRef",
    "TrainEnvPolicy",
    "Transition",
    "TransitionAction",
    "TransitionRef",
    "WorkItemLinks",
    "WorkItemRef",
    "Workflow",
    "WorkflowBudget",
    "WorkflowDefaults",
    "WorkflowMetadata",
    "artifact_digest",
    "artifact_type",
    "contracts_dir",
    "empreinte_de_skill",
    "file_outputs",
    "inline_bytes",
    "knowledge_digest",
    "load_openapi",
    "load_schema",
    "missing_outputs",
    "openapi_path",
    "schemas_dir",
    "type_of_filename",
    "type_of_value",
]
