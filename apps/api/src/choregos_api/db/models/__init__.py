# SPDX-License-Identifier: Apache-2.0
"""The data model (docs/plan/01 §1.3), split by domain (S47-31).

Every table that holds project data has `project_id` and is covered by PostgreSQL RLS
(`SET app.current_org`), set up by the initial migration.

Every submodule is imported here: relationships refer to classes by name, and an extension that
registers its models in the same `Base` needs the whole core registered first. Every name that
`choregos_api.db.models` exposed before the split, including the SQLAlchemy names the single file
imported, is re-exported, so no importer changes.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base, Json, PkMixin, TimestampMixin, utcnow, uuid7
from .agent_sessions import AgentSessionEvent, AgentSessionRecord, ConversationFeedback
from .agent_social import AgentNotification, AgentStar, AgentWatch
from .agents import (
    Agent,
    AgentCredential,
    AgentEvaluation,
    AgentRelease,
    AgentVersion,
    HostedBuildRecord,
    ProjectAgent,
)
from .assistant_launches import AssistantLaunchBinding
from .assistant_reads import AssistantRead
from .assistants import AssistantInstallation, AssistantToolBinding, ConnectedAccount, ConversationAction
from .audit import AuditLog, NetworkDecision
from .connectors import (
    ConnectorOperation,
    GitHubInstallation,
    OrgConnector,
    OrgConnectorProject,
    OrgEgressRule,
    ProjectOperationPolicy,
)
from .costs import BudgetAlert, CostLedger
from .gateway import BackendRegistryRow, GatewayKeyRow, ModelCatalogueRow
from .identity import (
    ApiToken,
    LinkedAccount,
    MachineAccount,
    Membership,
    Organization,
    OrgGroup,
    OrgGroupMember,
    OrgSlugAlias,
    User,
    UserSession,
)
from .knowledge import (
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDistillationPart,
    KnowledgeDocument,
    KnowledgeGrant,
    KnowledgeSource,
    KnowledgeUpdateProposal,
    MemoryFact,
    MemoryProfile,
    MemoryStore,
)
from .offered_knowledge import KnowledgeSeedTombstone
from .outposts import Outpost, OutpostCall, OutpostEnrolment, OutpostInstance
from .plugins import OrgPlugin, OrgPluginApproval, OrgPluginFile
from .projects import Connector, ModelProfileRow, OrgWorkflow, PolicyDef, Project, Template, WorkflowDef
from .releases import Deployment, Release
from .runs import (
    A2aPushConfig,
    Action,
    ActionEffect,
    ArtifactRecord,
    DecisionRecordRow,
    HandOffReceipt,
    ReceivedEvent,
    TriggerToken,
    WebhookDelivery,
)
from .secrets import OrgSecret, RemoteCredential
from .skills import Skill, SkillVersion
from .tasks import Delegation, TaskEvent, TaskSubscription
from .work_items import Event, Finding, HumanRequest, Run, RunEvent, WorkItem

ALL_TABLES = [
    Organization,
    User,
    Membership,
    Project,
    MachineAccount,
    OrgGroup,
    OrgGroupMember,
    AssistantInstallation,
    AssistantRead,
    AssistantLaunchBinding,
    AssistantToolBinding,
    TaskSubscription,
    TaskEvent,
    Delegation,
    ConnectedAccount,
    Connector,
    WorkflowDef,
    PolicyDef,
    ModelProfileRow,
    Template,
    WorkItem,
    Run,
    RunEvent,
    HumanRequest,
    Event,
    Finding,
    Release,
    Deployment,
    CostLedger,
    AuditLog,
    ApiToken,
    MemoryFact,
    WebhookDelivery,
    BackendRegistryRow,
    GatewayKeyRow,
    Agent,
    AgentVersion,
    ProjectAgent,
    Skill,
    SkillVersion,
    AgentCredential,
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeSeedTombstone,
    KnowledgeUpdateProposal,
    KnowledgeChunk,
    KnowledgeSource,
    OrgPlugin,
    OrgPluginFile,
    OrgPluginApproval,
    Outpost,
    OutpostInstance,
    OutpostEnrolment,
    OutpostCall,
]


def is_core_object(obj: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
    """Alembic's `include_object` filter: the core migrations describe only THEIR tables.

    An extension registers its models in the same `Base`, which is what puts them in `create_all()`,
    and creates its tables through its own migration branch (`choregos.migrations`). Without this
    filter, an autogeneration run with an extension installed would put its tables in a core
    migration, and the models/migrations comparison of the core would see them as missing. A table
    belongs to the core if its class is defined in `choregos_api`; an extension's tables do not.
    """
    if type_ != "table":
        return True
    return name in {
        mapper.class_.__tablename__
        for mapper in Base.registry.mappers
        if mapper.class_.__module__.startswith("choregos_api.")
    }


__all__ = [
    "ALL_TABLES",
    "A2aPushConfig",
    "Action",
    "ActionEffect",
    "Agent",
    "AgentCredential",
    "AgentEvaluation",
    "AgentNotification",
    "AgentRelease",
    "AgentSessionEvent",
    "AgentSessionRecord",
    "AgentStar",
    "AgentVersion",
    "AgentWatch",
    "Any",
    "ApiToken",
    "ArtifactRecord",
    "AssistantInstallation",
    "AssistantLaunchBinding",
    "AssistantRead",
    "AssistantToolBinding",
    "AuditLog",
    "BackendRegistryRow",
    "Base",
    "BigInteger",
    "Boolean",
    "BudgetAlert",
    "CheckConstraint",
    "ConnectedAccount",
    "Connector",
    "ConnectorOperation",
    "ConversationAction",
    "ConversationFeedback",
    "CostLedger",
    "DateTime",
    "DecisionRecordRow",
    "Delegation",
    "Deployment",
    "Event",
    "Finding",
    "Float",
    "ForeignKey",
    "GatewayKeyRow",
    "GitHubInstallation",
    "HandOffReceipt",
    "HostedBuildRecord",
    "HumanRequest",
    "Index",
    "Integer",
    "Json",
    "KnowledgeBase",
    "KnowledgeChunk",
    "KnowledgeDistillationPart",
    "KnowledgeDocument",
    "KnowledgeGrant",
    "KnowledgeSeedTombstone",
    "KnowledgeSource",
    "KnowledgeUpdateProposal",
    "LargeBinary",
    "LinkedAccount",
    "MachineAccount",
    "Mapped",
    "Membership",
    "MemoryFact",
    "MemoryProfile",
    "MemoryStore",
    "ModelCatalogueRow",
    "ModelProfileRow",
    "NetworkDecision",
    "OrgConnector",
    "OrgConnectorProject",
    "OrgEgressRule",
    "OrgGroup",
    "OrgGroupMember",
    "OrgPlugin",
    "OrgPluginApproval",
    "OrgPluginFile",
    "OrgSecret",
    "OrgSlugAlias",
    "OrgWorkflow",
    "Organization",
    "Outpost",
    "OutpostCall",
    "OutpostEnrolment",
    "OutpostInstance",
    "PkMixin",
    "PolicyDef",
    "Project",
    "ProjectAgent",
    "ProjectOperationPolicy",
    "ReceivedEvent",
    "Release",
    "RemoteCredential",
    "Run",
    "RunEvent",
    "Skill",
    "SkillVersion",
    "String",
    "TaskEvent",
    "TaskSubscription",
    "Template",
    "Text",
    "TimestampMixin",
    "TriggerToken",
    "UniqueConstraint",
    "User",
    "UserSession",
    "WebhookDelivery",
    "WorkItem",
    "WorkflowDef",
    "datetime",
    "false",
    "func",
    "is_core_object",
    "mapped_column",
    "relationship",
    "text",
    "utcnow",
    "uuid7",
]
