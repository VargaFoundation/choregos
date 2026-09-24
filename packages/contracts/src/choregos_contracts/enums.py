"""Énumérations partagées par tous les contrats Choregos."""

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


class StateKind(StrEnum):
    WORK = "work"
    WAIT = "wait"
    TERMINAL = "terminal"


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
    #: L'interpréteur du ticket est mort (activité en échec définitif, délai) : le ticket ne
    #: bougera plus tant qu'on ne le redémarre pas. Vu sur le banc du 2026-09-24, où deux
    #: tickets RH sont restés « en attente » sans qu'aucun écran ne dise qu'ils étaient morts.
    WORKITEM_WORKFLOW_FAILED = "choregos.workitem.workflow_failed"
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
    COST_RECORDED = "choregos.cost.recorded"
    COST_ALERT = "choregos.cost.alert"
    PROVISIONING_STEP = "choregos.project.provisioning.step"
    PROVISIONING_COMPLETED = "choregos.project.provisioning.completed"
    PROVISIONING_FAILED = "choregos.project.provisioning.failed"


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
