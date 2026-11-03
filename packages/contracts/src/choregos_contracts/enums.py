# SPDX-License-Identifier: Apache-2.0
"""Enumerations shared by all the Choregos contracts."""

from __future__ import annotations

from enum import StrEnum


class Size(StrEnum):
    S = "S"
    M = "M"
    L = "L"
    XL = "XL"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class StageRole(StrEnum):
    TRIAGE = "triage"
    REFINE = "refine"
    PLAN = "plan"
    IMPLEMENT = "implement"
    VERIFY = "verify"
    REVIEW = "review"
    FIX_CI = "fix_ci"
    ADDRESS_REVIEW = "address_review"
    RELEASE_NOTES = "release_notes"
    VERIFY_PROD = "verify_prod"
    CUSTOM = "custom"


class ActorType(StrEnum):
    AGENT = "agent"
    HUMAN = "human"
    SYSTEM = "system"
    #: A deterministic step: a command run in the runner's sandbox, no model (ADR 0056).
    JOB = "job"


class StateKind(StrEnum):
    WORK = "work"
    WAIT = "wait"
    TERMINAL = "terminal"


class StateOutcome(StrEnum):
    """How a TERMINAL state ends a work item: a success, a request turned down, or one dropped.

    Only a terminal state declares it; absent means `success`, the meaning every terminal state had
    before. The console shows a success green, and neither of the others: a declined access request
    is finished, not granted.
    """

    SUCCESS = "success"
    DECLINED = "declined"
    CANCELLED = "cancelled"


class StageStatus(StrEnum):
    DONE = "done"
    BLOCKED = "blocked"
    NEEDS_HUMAN = "needs_human"
    FAILED = "failed"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class FindingType(StrEnum):
    PERF = "perf"
    BUG = "bug"
    SECURITY = "security"
    TECH_DEBT = "tech-debt"
    DOCS = "docs"
    FLAKY_TEST = "flaky-test"
    UX = "ux"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(StrEnum):
    PENDING = "pending"
    CREATED = "created"
    DUPLICATE = "duplicate"
    DISMISSED = "dismissed"


class ReleaseStatus(StrEnum):
    COLLECTING = "collecting"
    DEPARTING = "departing"
    STAGING = "staging"
    AWAITING_APPROVAL = "awaiting_approval"
    PROMOTING = "promoting"
    VERIFYING = "verifying"
    DONE = "done"
    ROLLED_BACK = "rolled_back"
    FROZEN = "frozen"


class ProjectStatus(StrEnum):
    DRAFT = "draft"
    PROVISIONING = "provisioning"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    ARCHIVED = "archived"


class ConnectorKind(StrEnum):
    TRACKER = "tracker"
    SCM = "scm"
    CI = "ci"
    CD = "cd"
    RUNTIME = "runtime"
    MEMORY = "memory"
    NOTIFY = "notify"
    GATEWAY = "gateway"


class Role(StrEnum):
    ORG_ADMIN = "org_admin"
    PROJECT_OWNER = "project_owner"
    DEVELOPER = "developer"
    RELEASE_CAPTAIN = "release_captain"
    VIEWER = "viewer"


class HumanRequestKind(StrEnum):
    APPROVAL = "approval"
    QUESTION = "question"
    SCOPE_CHANGE = "scope_change"
    #: A gesture a person DOES (handing over a badge, unpacking a workstation), with its values and
    #: what they attest (S20-06).
    TASK = "task"


class MemoryKind(StrEnum):
    DECISION = "decision"
    CONVENTION = "convention"
    INCIDENT = "incident"
    TICKET_SUMMARY = "ticket_summary"
    RUN_LESSON = "run_lesson"
    FLAKY_TEST = "flaky_test"
    HOTSPOT = "hotspot"
    FINDING = "finding"
    OTHER = "other"


class ExecutorKind(StrEnum):
    TEKTON = "tekton"
    K8S_JOB = "k8s_job"
    LOCAL_DOCKER = "local_docker"
    ACA = "aca"
    #: kubernetes-sigs/agent-sandbox: warm pool, suspend across a human question (ADR 0016).
    AGENT_SANDBOX = "agent_sandbox"
    FAKE = "fake"


class ApiFormat(StrEnum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    GEMINI = "gemini"


class EventType(StrEnum):
    WORKITEM_CREATED = "choregos.workitem.created"
    WORKITEM_STATE_CHANGED = "choregos.workitem.state_changed"
    WORKITEM_CLOSED = "choregos.workitem.closed"
    WORKITEM_HUMAN_REQUESTED = "choregos.workitem.human_requested"
    WORKITEM_HUMAN_DECIDED = "choregos.workitem.human_decided"
    #: The ticket's interpreter is dead (activity failed for good, timeout): the ticket will not
    #: move again until it is restarted. Seen on the 2026-09-24 bench, where two HR tickets stayed
    #: "pending" with no screen saying they were dead.
    WORKITEM_WORKFLOW_FAILED = "choregos.workitem.workflow_failed"
    #: The ticket changed definition (`migrate`): the pin follows, the state is remapped.
    WORKITEM_MIGRATED = "choregos.workitem.migrated"
    #: An impossible migration (state missing from the target, no mapping): the ticket stays alive,
    #: on its own definition. This event is what says so.
    WORKITEM_MIGRATION_REFUSED = "choregos.workitem.migration_refused"
    RUN_QUEUED = "choregos.run.queued"
    RUN_STARTED = "choregos.run.started"
    RUN_PROGRESS = "choregos.run.progress"
    RUN_FINISHED = "choregos.run.finished"
    FINDING_REPORTED = "choregos.finding.reported"
    FINDING_CREATED = "choregos.finding.created"
    FINDING_DUPLICATE = "choregos.finding.duplicate"
    FINDING_DISMISSED = "choregos.finding.dismissed"
    RELEASE_COLLECTED = "choregos.release.collected"
    RELEASE_DEPARTED = "choregos.release.departed"
    RELEASE_STAGED = "choregos.release.staged"
    RELEASE_APPROVAL_REQUESTED = "choregos.release.approval_requested"
    RELEASE_PROMOTED = "choregos.release.promoted"
    RELEASE_VERIFIED = "choregos.release.verified"
    RELEASE_ROLLED_BACK = "choregos.release.rolled_back"
    RELEASE_FROZEN = "choregos.release.frozen"
    TOOL_CALLED = "choregos.tool.called"
    NETWORK_DENIED = "choregos.network.denied"
    #: Prompt-injection patterns in what the agent was about to read (ticket, documents,
    #: memory): always logged, blocking according to `sandbox.prompt_injection`.
    SECURITY_INJECTION_SUSPECTED = "choregos.security.injection_suspected"
    COST_RECORDED = "choregos.cost.recorded"
    COST_ALERT = "choregos.cost.alert"
    PROVISIONING_STEP = "choregos.project.provisioning.step"
    PROVISIONING_COMPLETED = "choregos.project.provisioning.completed"
    PROVISIONING_FAILED = "choregos.project.provisioning.failed"
    #: Governed actions (ADR 0035): proposed, decided, each effect done or compensated,
    #: and its end. The evidence file of an arrival or a departure is read from these events.
    ACTION_PROPOSED = "choregos.action.proposed"
    ACTION_DECIDED = "choregos.action.decided"
    ACTION_EFFECT_DONE = "choregos.action.effect_done"
    ACTION_EFFECT_COMPENSATED = "choregos.action.effect_compensated"
    ACTION_SUCCEEDED = "choregos.action.succeeded"
    ACTION_FAILED = "choregos.action.failed"


class ActionStatus(StrEnum):
    """The lifecycle of a governed action (ADR 0035)."""

    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"
    APPROVED = "approved"
    RUNNING = "running"
    #: Its effects are done; it waits for the PROOF that they served: a report to come, an
    #: outside event, until a deadline (S20-08).
    AWAITING_EVIDENCE = "awaiting_evidence"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ActionOrigin(StrEnum):
    """Where an action comes from: an ontology operation, a tool under validation (ADR 0034), a
    workflow stage effect."""

    ONTOLOGY = "ontology"
    TOOL = "tool"
    TRANSITION = "transition"


class InboundEventType(StrEnum):
    ITEM_CREATED = "tracker.item.created"
    ITEM_UPDATED = "tracker.item.updated"
    ITEM_MOVED = "tracker.item.moved"
    ITEM_COMMENTED = "tracker.item.commented"
    ITEM_LABELED = "tracker.item.labeled"
    ITEM_CLOSED = "tracker.item.closed"
    PR_OPENED = "scm.pr.opened"
    PR_SYNCHRONIZED = "scm.pr.synchronized"
    PR_REVIEW_SUBMITTED = "scm.pr.review_submitted"
    PR_MERGED = "scm.pr.merged"
    PR_CLOSED = "scm.pr.closed"
    CHECK_COMPLETED = "scm.check.completed"
    CI_STARTED = "ci.run.started"
    CI_SUCCEEDED = "ci.run.succeeded"
    CI_FAILED = "ci.run.failed"
    CD_SYNCED = "cd.app.synced"
    CD_DEGRADED = "cd.app.degraded"
    ROLLOUT_COMPLETED = "cd.rollout.completed"
    ROLLOUT_ABORTED = "cd.rollout.aborted"
    ALERT_FIRED = "alert.fired"
    ALERT_RESOLVED = "alert.resolved"
    HUMAN_DECISION = "human.decision"
