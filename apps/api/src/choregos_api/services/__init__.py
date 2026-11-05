# SPDX-License-Identifier: Apache-2.0
"""Services shared by the routers: queries, conversions, side effects.

The routers stay thin; all the logic that can be reused (by the CLI, the orchestrator or the
webhooks) lives here, in one module per subject, re-exported so that `choregos_api.services.X`
stays the address of everything.
"""

from __future__ import annotations

from .costs import (
    estimate_cost,
    record_cost,
)
from .decision_ledger import (
    DecidedBy,
    record_decisions,
    record_human_verdict,
    record_reopened,
    record_reroute,
    record_routing,
    record_terminal_outcome,
    record_transition_taken,
)
from .definitions import (
    DEFAULT_POLICY_PRESET,
    DEFAULT_WORKFLOW,
    active_policy,
    active_workflow,
    active_workflow_named,
    default_workflow,
    effective_policy,
    effective_policy_and_clamps,
    ensure_defaults,
    policy_engine,
    policy_model,
    project_config,
    publish_workflow,
    report_unenforced_policy,
    work_item_workflow,
    workflow_model,
    workflow_yaml,
)
from .events import (
    persist_event,
)
from .human_requests import new_human_request
from .journey import journey, move_edge
from .memory import (
    MIN_WORK_ITEMS_PER_GROUP,
    _ab_verdict,
    _window_stats,
    memory_ab_comparison,
)
from .projects import (
    first_pass_merge_rate,
    project_dto,
    project_stats,
)
from .routing import (
    WorkItemBirth,
    choose_workflow,
    new_work_item,
    record_labels,
)
from .tickets import (
    SOFTWARE_DOCUMENTS,
    _temporal_status,
    create_work_item,
    human_request_dto,
    internal_work_item_key,
    outputs_to_store,
    release_dto,
    run_dto,
    run_summary,
    store_outputs,
    timeline,
    tool_connectors_of,
    totals_from,
    tracker_is_internal,
    work_item_dto,
)

__all__ = [
    "DEFAULT_POLICY_PRESET",
    "DEFAULT_WORKFLOW",
    "MIN_WORK_ITEMS_PER_GROUP",
    "SOFTWARE_DOCUMENTS",
    "DecidedBy",
    "WorkItemBirth",
    "_ab_verdict",
    "_temporal_status",
    "_window_stats",
    "active_policy",
    "active_workflow",
    "active_workflow_named",
    "choose_workflow",
    "create_work_item",
    "default_workflow",
    "effective_policy",
    "effective_policy_and_clamps",
    "ensure_defaults",
    "estimate_cost",
    "first_pass_merge_rate",
    "human_request_dto",
    "internal_work_item_key",
    "journey",
    "memory_ab_comparison",
    "move_edge",
    "new_human_request",
    "new_work_item",
    "outputs_to_store",
    "persist_event",
    "policy_engine",
    "policy_model",
    "project_config",
    "project_dto",
    "project_stats",
    "publish_workflow",
    "record_cost",
    "record_decisions",
    "record_human_verdict",
    "record_labels",
    "record_reopened",
    "record_reroute",
    "record_routing",
    "record_terminal_outcome",
    "record_transition_taken",
    "release_dto",
    "report_unenforced_policy",
    "run_dto",
    "run_summary",
    "store_outputs",
    "timeline",
    "tool_connectors_of",
    "totals_from",
    "tracker_is_internal",
    "work_item_dto",
    "work_item_workflow",
    "workflow_model",
    "workflow_yaml",
]
