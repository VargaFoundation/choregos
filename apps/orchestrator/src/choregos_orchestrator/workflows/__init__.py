# SPDX-License-Identifier: Apache-2.0
"""Workflows Temporal de Choregos."""

from __future__ import annotations

from ..activities.interpretation import (
    escalation_context,
    load_context,
    record_migration,
    record_workflow_failure,
    signal_train,
)
from .action import ActionWorkflow
from .evals import EvalMatrix
from .findings import FindingsTriage
from .interpreter import WorkflowInterpreter
from .knowledge import KnowledgeSync
from .memory import MemoryIngestion
from .provisioning import ProjectProvisioning
from .reconciliation import TrackerReconciliation
from .train import ReleaseTrain
from .trial import AgentTrial

ALL_WORKFLOWS = [
    WorkflowInterpreter,
    ActionWorkflow,
    ReleaseTrain,
    FindingsTriage,
    ProjectProvisioning,
    MemoryIngestion,
    EvalMatrix,
    TrackerReconciliation,
    AgentTrial,
    KnowledgeSync,
]

WORKFLOW_ACTIVITIES = [
    load_context,
    record_workflow_failure,
    record_migration,
    signal_train,
    escalation_context,
]

__all__ = [
    "ALL_WORKFLOWS",
    "WORKFLOW_ACTIVITIES",
    "ActionWorkflow",
    "AgentTrial",
    "EvalMatrix",
    "FindingsTriage",
    "KnowledgeSync",
    "MemoryIngestion",
    "ProjectProvisioning",
    "ReleaseTrain",
    "TrackerReconciliation",
    "WorkflowInterpreter",
]
