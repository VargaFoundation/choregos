"""Workflows Temporal de Choregos."""

from __future__ import annotations

from ..activities.interpretation import load_context, record_workflow_failure, signal_train
from .evals import EvalMatrix
from .findings import FindingsTriage
from .interpreter import WorkflowInterpreter
from .memory import MemoryIngestion
from .provisioning import ProjectProvisioning
from .reconciliation import TrackerReconciliation
from .train import ReleaseTrain

ALL_WORKFLOWS = [
    WorkflowInterpreter,
    ReleaseTrain,
    FindingsTriage,
    ProjectProvisioning,
    MemoryIngestion,
    EvalMatrix,
    TrackerReconciliation,
]

WORKFLOW_ACTIVITIES = [load_context, record_workflow_failure, signal_train]

__all__ = [
    "ALL_WORKFLOWS",
    "WORKFLOW_ACTIVITIES",
    "EvalMatrix",
    "FindingsTriage",
    "MemoryIngestion",
    "ProjectProvisioning",
    "ReleaseTrain",
    "TrackerReconciliation",
    "WorkflowInterpreter",
]
