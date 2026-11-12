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
    CRITICAL = "critical"


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
    #: A decision model answers typed questions about a named state, through the gateway, in the
    #: orchestrator: no pod, no agent loop (ADR 0121).
    DECISION = "decision"


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
    #: An action that ended and did not succeed. Its data carries the `status` it ended with:
    #: `failed` (what it had done was compensated, and it says what it could not undo) or `in_doubt`
    #: (nothing was compensated, ADR 0149 §5). A consumer that reads the type alone reads "not
    #: succeeded", which is true of both.
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
    #: One of its effects was sent and nobody knows whether it was executed: a write through an
    #: outpost that an instance claimed and never answered (ADR 0149 §5). Terminal: it is never
    #: replayed and nothing it had done is compensated automatically; a person checks the system the
    #: write was for.
    IN_DOUBT = "in_doubt"


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


class OutpostStatus(StrEnum):
    """An outpost, as its organisation keeps it (ADR 0147)."""

    ACTIVE = "active"
    #: Nothing more is handed to it and the poll's stop flag tells its instances; what they already
    #: hold may finish (ADR 0148 §10). Resumed, it is `active` again.
    PAUSED = "paused"
    #: Terminal. Its instances are revoked and its epoch moved (ADR 0147 §5); a connector that still
    #: names it is refused as it is for a paused one (ADR 0149 §7).
    REVOKED = "revoked"


class OutpostInstanceStatus(StrEnum):
    """One copy of the daemon `choregos-outpost` (ADR 0147 §2, §4)."""

    #: Enrolled with a token, handed nothing: a person has not compared its fingerprint yet.
    PENDING = "pending"
    ACTIVE = "active"
    #: Terminal: its signature is refused from the next request.
    REVOKED = "revoked"


class OutpostCapability(StrEnum):
    """What an instance may announce and an organisation enable (ADR 0147 §11)."""

    TOOLS = "tools"
    #: Reserved (ADR 0149 §12): announced by no daemon of protocol 1 and enabled by no organisation.
    #: It is named here so that no other meaning takes the word.
    RUNS = "runs"


class OutpostCallKind(StrEnum):
    """What a call asks of an outpost in protocol 1. A daemon refuses a kind it does not know with
    `OutpostRefusalCode.UNSUPPORTED_KIND`: a later kind never breaks an older daemon."""

    CALL_TOOL = "call_tool"
    #: Discovery: the tools the endpoint's server lists and the outpost's policy lets through.
    LIST_TOOLS = "list_tools"


class OutpostCallState(StrEnum):
    """The short life of a call through an outpost, a row of the rendezvous (ADR 0149 §4-5)."""

    #: Written by its caller, claimed by no instance yet.
    PENDING = "pending"
    #: Leased to one instance, under the outpost's epoch of that moment.
    CLAIMED = "claimed"
    #: The claiming instance answered within its lease: a result, a server's error or a result too large.
    ANSWERED = "answered"
    #: The outpost's own policy refused it: nothing reached its server.
    REFUSED = "refused"
    #: No active instance claimed it in time: nothing reached any server (`OutpostOffline`).
    EXPIRED = "expired"
    #: A claimed read got no answer within its lease; it may be asked again (`OutpostTimeout`).
    TIMED_OUT = "timed_out"
    #: A claimed write got no answer within its lease: nobody knows whether the server executed it. It
    #: is never leased again (`OutpostInDoubt`).
    IN_DOUBT = "in_doubt"


class OutpostAnswerOutcome(StrEnum):
    """What a daemon reports of a call it took to its server. Each of them says the call left the
    outpost's policy checks; a call that did not is a refusal (`OutpostRefusalCode`), never an answer."""

    #: The server answered: a tool's result (which may itself say `isError`), or the tool listing.
    RESULT = "result"
    #: The server answered a JSON-RPC error: it says it did not do the call.
    ERROR = "error"
    #: The call was sent, or begun, and the daemon holds no answer (a timeout at the endpoint, a lost
    #: connection, a restart in the middle, a read abandoned because the door cancelled it): executed
    #: or not, nobody knows.
    UNANSWERED = "unanswered"
    #: The server answered and its result weighs more than the outpost's ceiling or the door's cap: the
    #: call was executed and its result is not sent.
    OVERSIZED = "oversized"


class OutpostRefusalCode(StrEnum):
    """Why an outpost did not execute a call it received: a closed list (ADR 0148 §9). A code names a
    class of refusal and never the rule that produced it (ADR 0147 §10). Whatever the code, the
    endpoint's server did not receive the call."""

    #: The policy file has no endpoint under this alias.
    UNKNOWN_ENDPOINT = "unknown_endpoint"
    #: The tool is outside the endpoint's patterns.
    TOOL_NOT_ALLOWED = "tool_not_allowed"
    #: The endpoint denies writes and its server does not declare the tool read-only.
    WRITE_DENIED = "write_denied"
    #: The arguments weigh more than the endpoint's ceiling.
    PAYLOAD_TOO_LARGE = "payload_too_large"
    #: The outpost cannot take the call now: its concurrency ceiling is reached, or it is stopping. It
    #: also covers a call the daemon claimed and never began because it was stopping or was restarted.
    BUSY = "busy"
    #: The outpost could not reach or open its own server, and sent it nothing.
    ENDPOINT_UNAVAILABLE = "endpoint_unavailable"
    #: The outpost could not write its journal or its audit line, so it did not make the call: what it
    #: cannot record, it does not do.
    CANNOT_RECORD = "cannot_record"
    #: The daemon does not know this kind of call (a kind later than its protocol).
    UNSUPPORTED_KIND = "unsupported_kind"
    #: The call is not signed by a key the outpost's policy file pins, or its signature binds another
    #: call, another instance or another envelope. The envelope was not opened (ADR 0148 §13).
    SIGNATURE_REFUSED = "signature_refused"
    #: The envelope does not open with this instance's key, or what it holds is not a call.
    CALL_UNREADABLE = "call_unreadable"
    #: The call's deadline had passed before the outpost could start it.
    DEADLINE_PASSED = "deadline_passed"
    #: The door cancelled the call before the outpost started it.
    CANCELLED = "cancelled"


class OutpostDoorCode(StrEnum):
    """The `code` of every problem the outposts' door answers (`outpost-door.yaml`, ADR 0148)."""

    #: 422: the body is not what the verb's schema describes.
    INVALID_REQUEST = "invalid_request"
    #: 413: the body is above the verb's cap; refused before it is parsed.
    BODY_TOO_LARGE = "body_too_large"
    #: 401: no signature, one that does not verify, or a key the door does not know.
    INVALID_SIGNATURE = "invalid_signature"
    #: 401: the signature verifies and binds another method, URL or body.
    REQUEST_MISMATCH = "request_mismatch"
    #: 401: `iat` is more than 60 seconds from the door's clock; the problem carries the door's time.
    STALE_IAT = "stale_iat"
    #: 401: the nonce is absent or too old; the answer carries a fresh one.
    STALE_NONCE = "stale_nonce"
    #: 401: the sequence is not above the last one the door took from this instance.
    REPLAYED_SEQUENCE = "replayed_sequence"
    #: 401: the enrolment token is unknown, already used or expired; the door does not say which.
    INVALID_ENROLMENT_TOKEN = "invalid_enrolment_token"  # noqa: S105 - a problem code, not a secret
    #: 409: this signing key is already an approved or revoked instance, or is pending at another outpost.
    #: A key still pending at the token's own outpost is enrolled again as the same instance. A revoked
    #: key does not come back for as long as its outpost exists; once that outpost is deleted the key is
    #: unknown again (ADR 0147 §5).
    KEY_ALREADY_ENROLLED = "key_already_enrolled"
    #: 403: nobody has approved this instance yet; it is handed nothing.
    INSTANCE_PENDING = "instance_pending"
    #: 403: this instance, or its outpost, was revoked.
    INSTANCE_REVOKED = "instance_revoked"
    #: 426: the daemon's protocol is below the door's minimum; the problem names the window.
    PROTOCOL_UNSUPPORTED = "protocol_unsupported"
    #: 429: too many requests from this instance; the problem says when to come back.
    RATE_LIMITED = "rate_limited"
    #: 409: another poll is waiting under this instance's key.
    POLL_CONFLICT = "poll_conflict"
    #: 404: no such call for this outpost. Another outpost's call answers the same.
    NOT_FOUND = "not_found"
    #: 409: the call is not leased to this instance any more: its lease ended, or the epoch moved.
    LEASE_LOST = "lease_lost"


class OutpostAuditEvent(StrEnum):
    """What a line of an outpost's local audit log records about an assignment (ADR 0147 §8)."""

    RECEIVED = "received"
    EXECUTED = "executed"
    REFUSED = "refused"
    #: A call id the journal already held was answered from it; the server was not called again.
    REPLAYED = "replayed"
