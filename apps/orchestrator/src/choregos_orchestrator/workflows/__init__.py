"""Workflows Temporal de Choregos."""

from __future__ import annotations

from .evals import EvalMatrix
from .findings import FindingsTriage
from .interpreter import WorkflowInterpreter, load_context, signal_train
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

WORKFLOW_ACTIVITIES = [load_context, signal_train]

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
