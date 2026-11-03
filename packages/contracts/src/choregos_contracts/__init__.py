# SPDX-License-Identifier: Apache-2.0
"""Choregos contracts: JSON Schemas, OpenAPI and Python types.

`packages/contracts` is the **source of truth** for the interfaces between the streams.
Any change goes through a PR labelled `contract-change` (docs/plan/01).
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
    StateOutcome,
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
from .project import (
    A2A_MAX_FILE_BYTES_CEILING,
    A2A_MAX_FILE_BYTES_DEFAULT,
    A2aConfig,
    AgentConfig,
    DodConfig,
    ModelProfile,
    ModelsConfig,
    ProjectConfig,
    RepoConfig,
    ScheduleConfig,
    StartRule,
    TriggerConfig,
)
from .skill_files import (
    INERT_BINARY_EXTENSIONS,
    MAX_BINARY_BYTES,
    check_inert_binary,
    encode_binary,
    is_inert_binary_path,
    skill_file_bytes,
)
from .stage import (
    STAGE_INPUT_SCHEMA,
    STAGE_RESULT_SCHEMA,
    AgentRef,
    ArtifactFile,
    Artifacts,
    Budget,
    Callbacks,
    ContextPack,
    DeclaredCost,
    Diagnostics,
    Evidence,
    Finding,
    HostedCall,
    JobSpec,
    KnowledgeRef,
    LaunchSpec,
    McpServerRef,
    MemoryItem,
    ModelRef,
    Permissions,
    PlaybookRef,
    ProjectRef,
    PromptCall,
    Question,
    RelatedItem,
    RemoteCall,
    RepoRef,
    ScopeChangeRequest,
    SkillRef,
    StageContext,
    StageInput,
    StageMemory,
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
    skill_digest,
)
from .workflow import (
    RUN_ACTORS,
    ActionApprovalSpec,
    ActionApprover,
    ActionEffectSpec,
    AgentActor,
    GateSpec,
    HumanActor,
    JobActor,
    JobTools,
    Retry,
    SpawnSpec,
    State,
    SystemActor,
    TaskSpec,
    Transition,
    TransitionAction,
    Workflow,
    WorkflowA2a,
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
    "plugin.schema.json",
    "plugin-server.schema.json",
    "plugin-index.schema.json",
    "plugin-evals.schema.json",
    "plugin-tracker-profile.schema.json",
)


def contracts_dir() -> Path:
    """Contracts root: `packages/contracts` in development, the installed package otherwise."""
    root = Path(str(resources.files("choregos_contracts"))).resolve()
    if (root / "schemas").is_dir():  # wheel: the schemas are bundled
        return root
    return root.parent.parent  # src/choregos_contracts -> packages/contracts


def schemas_dir() -> Path:
    return contracts_dir() / "schemas"


def load_schema(name: str) -> dict[str, Any]:
    """Load a JSON Schema by file name (e.g. `workflow.schema.json`)."""
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
    "A2A_MAX_FILE_BYTES_CEILING",
    "A2A_MAX_FILE_BYTES_DEFAULT",
    "ARTIFACT_NAME_PATTERN",
    "ARTIFACT_TYPES",
    "INERT_BINARY_EXTENSIONS",
    "MAX_BINARY_BYTES",
    "RUN_ACTORS",
    "STAGE_INPUT_SCHEMA",
    "STAGE_RESULT_SCHEMA",
    "A2aConfig",
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
    "DeclaredCost",
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
    "HostedCall",
    "HumanActor",
    "HumanDecision",
    "HumanRequestKind",
    "InboundEvent",
    "InboundEventType",
    "JobActor",
    "JobSpec",
    "JobTools",
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
    "PromptCall",
    "Question",
    "RelatedItem",
    "ReleaseStatus",
    "RemoteCall",
    "RepoConfig",
    "RepoRef",
    "Retry",
    "ReviewPolicy",
    "Risk",
    "Role",
    "RunStatus",
    "SandboxPolicy",
    "ScheduleConfig",
    "ScopeChangeRequest",
    "ScopePolicy",
    "Severity",
    "Size",
    "SkillRef",
    "SpawnSpec",
    "StageContext",
    "StageInput",
    "StageMemory",
    "StageOutputs",
    "StageResult",
    "StageRole",
    "StageStatus",
    "StartRule",
    "State",
    "StateKind",
    "StateOutcome",
    "SystemActor",
    "TaskSpec",
    "ToolsRef",
    "TrainEnvPolicy",
    "Transition",
    "TransitionAction",
    "TransitionRef",
    "TriggerConfig",
    "WorkItemLinks",
    "WorkItemRef",
    "Workflow",
    "WorkflowA2a",
    "WorkflowBudget",
    "WorkflowDefaults",
    "WorkflowMetadata",
    "artifact_digest",
    "artifact_type",
    "check_inert_binary",
    "contracts_dir",
    "empreinte_de_skill",
    "encode_binary",
    "file_outputs",
    "inline_bytes",
    "is_inert_binary_path",
    "knowledge_digest",
    "load_openapi",
    "load_schema",
    "missing_outputs",
    "openapi_path",
    "schemas_dir",
    "skill_digest",
    "skill_file_bytes",
    "type_of_filename",
    "type_of_value",
]
