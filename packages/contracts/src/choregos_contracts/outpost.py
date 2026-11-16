# SPDX-License-Identifier: Apache-2.0
"""Outposts: the door's protocol 1 and the outpost's own files (ADRs 0147, 0148, 0149).

An outpost is a place a person enrols, which only dials out. Two programs nobody upgrades together
speak this contract: the door (`/outposts/v1`, in the API) and the daemon `choregos-outpost`, which
lags it by weeks in someone else's network. What both must agree on to the byte is therefore here,
and nowhere else:

- the outpost's local policy file (`schemas/outpost-policy.schema.json`);
- a call and its answer as the daemon sees them once the envelope is open
  (`schemas/outpost-call.schema.json`, `schemas/outpost-answer.schema.json`), a refusal
  (`schemas/outpost-refusal.schema.json`) and a line of the local audit log
  (`schemas/outpost-audit-line.schema.json`);
- the enrolment (`schemas/outpost-enrolment-request.schema.json`, `…-answer.schema.json`);
- the messages of the three other verbs, the two signatures, the sealing and the bounds, which
  `outpost-door.yaml` describes and the constants below repeat.

Two signatures cross the door, and they are not the same thing. A *request's* signature is the
instance's: every request an outpost sends is signed with the key it enrolled (ADR 0148 §4). A
*call's* signature is the door's: every call the door hands over is signed with a key the outpost's
own policy file pins, and an outpost makes no other (ADR 0148 §13).

The models read what the door sends leniently (a field a later door adds is ignored, ADR 0148 §3)
and what the daemon sends strictly (a hostile outpost meets a closed shape, ADR 0148 §9). This
module holds no cryptography: `choregos_adapters.sealing` seals, and whoever signs brings its own
library; the helpers here only compute what a signature binds.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Annotated, Any, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .enums import (
    OutpostAnswerOutcome,
    OutpostAuditEvent,
    OutpostCallKind,
    OutpostDoorCode,
    OutpostRefusalCode,
)
from .workflow import Strict

# ───────────────────────── protocol 1: names and bounds ─────────────────────────

#: The protocol this contract describes; `outpost-door.yaml` is its document (`info.version: v1`).
OUTPOST_PROTOCOL: Final = 1
#: The prefix every verb of the door is served under.
OUTPOST_DOOR_PREFIX: Final = "/outposts/v1"

#: Both signatures are compact JWS made with an Ed25519 key.
SIGNATURE_ALGORITHM: Final = "EdDSA"
#: A request's signature, the instance's: the request header that carries it, the answer header
#: that carries the nonce, and the `typ` of its protected header.
SIGNATURE_HEADER: Final = "Outpost-Signature"
NONCE_HEADER: Final = "Outpost-Nonce"
REQUEST_SIGNATURE_TYPE: Final = "choregos-outpost+jwt"
#: What every request's signature binds, and what all but the enrolment's also carry (ADR 0148 §4).
REQUEST_SIGNATURE_CLAIMS: Final = ("htm", "htu", "body_sha256", "iat")
REQUEST_SIGNATURE_SESSION_CLAIMS: Final = ("nonce", "seq")
#: A call's signature, the door's (ADR 0148 §13): the `typ` of its protected header, whose `kid` is
#: the signing key itself, and its claims. Nothing else is in it: freshness is the `deadline` inside
#: the envelope, which `sealed_sha256` covers.
CALL_SIGNATURE_TYPE: Final = "choregos-outpost-call+jwt"
CALL_SIGNATURE_CLAIMS: Final = ("call", "instance", "sealed_sha256")
#: The most door keys a policy file pins: the one that signs, and room for a rotation.
MAX_DOOR_KEYS: Final = 4

#: `iat` is refused when it is further than this from the door's clock, either way.
IAT_WINDOW_S: Final = 60
#: How long a nonce the door issued stays fresh.
NONCE_TTL_S: Final = 300
#: The longest a poll waits before it answers with nothing.
MAX_WAIT_S: Final = 25
#: The longest a lease lasts, whatever a daemon asks for: nothing a daemon sends extends one.
MAX_LEASE_S: Final = 300
#: An enrolment token lives 15 minutes at most (ADR 0147 §4).
ENROLMENT_TTL_S: Final = 900
#: The most calls one instance holds at once, hence the most a poll hands over.
MAX_CONCURRENCY: Final = 64
#: A body above its verb's cap is refused before it is parsed (ADR 0148 §9).
MAX_BODY_BYTES: Final[dict[str, int]] = {
    "enrol": 16_384,
    "poll": 65_536,
    "answer": 2_097_152,
    "refuse": 4_096,
}
#: The most one call weighs as a poll's answer carries it: its id, its envelope and its signature
#: together. A call whose envelope would be larger is refused where it is admitted, on the platform's
#: side, and never handed over.
MAX_SEALED_CALL_BYTES: Final = 2_097_152
#: The most a poll's answer weighs beside its calls (the window, the clock, the stop flag, the
#: cancellations, whatever a later door adds).
MAX_POLL_ANSWER_REST_BYTES: Final = 65_536
#: The most calls an instance reports as held, hence the most a poll's answer cancels.
MAX_RUNNING: Final = 256
#: The highest sequence a request's signature carries: a whole number JSON keeps exact. The door keeps
#: it as a 64-bit integer.
MAX_SEQUENCE: Final = 2**53 - 1
#: `HKDF` `info` of `choregos_adapters.sealing`, reused as it is (ADR 0148 §5).
SEALING_INFO: Final = "choregos/assignment/v1"

#: The HTTP status each problem code of the door travels under.
DOOR_STATUS: Final[dict[OutpostDoorCode, int]] = {
    OutpostDoorCode.INVALID_SIGNATURE: 401,
    OutpostDoorCode.REQUEST_MISMATCH: 401,
    OutpostDoorCode.STALE_IAT: 401,
    OutpostDoorCode.STALE_NONCE: 401,
    OutpostDoorCode.REPLAYED_SEQUENCE: 401,
    OutpostDoorCode.INVALID_ENROLMENT_TOKEN: 401,
    OutpostDoorCode.INSTANCE_PENDING: 403,
    OutpostDoorCode.INSTANCE_REVOKED: 403,
    OutpostDoorCode.NOT_FOUND: 404,
    OutpostDoorCode.KEY_ALREADY_ENROLLED: 409,
    OutpostDoorCode.POLL_CONFLICT: 409,
    OutpostDoorCode.LEASE_LOST: 409,
    OutpostDoorCode.BODY_TOO_LARGE: 413,
    OutpostDoorCode.INVALID_REQUEST: 422,
    OutpostDoorCode.PROTOCOL_UNSUPPORTED: 426,
    OutpostDoorCode.RATE_LIMITED: 429,
}

# ───────────────────────── shapes shared by the schemas ─────────────────────────

OUTPOST_SLUG_PATTERN: Final = r"^[a-z0-9][a-z0-9-]{0,62}$"
ENDPOINT_ALIAS_PATTERN: Final = r"^[a-z][a-z0-9-]{0,62}$"
#: A call's id and an instance's id: opaque, and safe as one path segment.
OUTPOST_ID_PATTERN: Final = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$"
#: A raw 32-byte public key (Ed25519 or X25519), base64url without padding: 43 characters, the last
#: of which carries four bits. A key has one spelling, so that two keys are compared as written.
PUBLIC_KEY_PATTERN: Final = r"^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$"
FINGERPRINT_PATTERN: Final = r"^SHA256:[A-Za-z0-9_-]{43}$"
SHA256_PATTERN: Final = r"^[0-9a-f]{64}$"
CALL_KIND_PATTERN: Final = r"^[a-z][a-z0-9_]{0,31}$"
#: A tool-name pattern of the policy file: `*` stands for any run of characters, nothing else is
#: special, and the whole name must match.
TOOL_PATTERN_PATTERN: Final = r"^[A-Za-z0-9_.*-]{1,128}$"
DAEMON_VERSION_PATTERN: Final = r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,63}$"
SEALED_PATTERN: Final = r"^[A-Za-z0-9_-]{80,}$"
#: A compact JWS whose signature is an Ed25519 one (64 bytes).
CALL_SIGNATURE_PATTERN: Final = r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{86}$"

OutpostSlug = Annotated[str, Field(pattern=OUTPOST_SLUG_PATTERN)]
EndpointAlias = Annotated[str, Field(pattern=ENDPOINT_ALIAS_PATTERN)]
OutpostId = Annotated[str, Field(pattern=OUTPOST_ID_PATTERN)]
PublicKey = Annotated[str, Field(pattern=PUBLIC_KEY_PATTERN)]
Fingerprint = Annotated[str, Field(pattern=FINGERPRINT_PATTERN)]
Sha256 = Annotated[str, Field(pattern=SHA256_PATTERN)]
CallKindName = Annotated[str, Field(pattern=CALL_KIND_PATTERN)]
ToolName = Annotated[str, Field(min_length=1, max_length=128)]
ToolPattern = Annotated[str, Field(pattern=TOOL_PATTERN_PATTERN)]
DaemonVersion = Annotated[str, Field(pattern=DAEMON_VERSION_PATTERN)]
Protocol = Annotated[int, Field(ge=1)]
#: The one capability a daemon of protocol 1 announces; `runs` is reserved (`OutpostCapability`).
AnnouncedCapability = Literal["tools"]
AssertedName = Annotated[str, Field(max_length=256)]


class Lenient(BaseModel):
    """What the door sends and a daemon reads: a field this version does not know is ignored, so that
    a need can enter an answer as a field before it ever becomes a verb (ADR 0148 §3)."""

    model_config = ConfigDict(extra="ignore")


# ───────────────────────── the outpost's policy file ─────────────────────────


class OutpostEndpointAuth(Strict):
    """The credential that opens an endpoint: it exists in the outpost's files only (ADR 0147 §7)."""

    #: `Authorization` sends `Bearer <the file's content>`; another name sends the content raw, as the
    #: direct `mcp` connector does.
    header: Annotated[str, Field(pattern=r"^[A-Za-z0-9-]{1,64}$")] = "Authorization"
    #: An absolute path, read by the daemon; its content never leaves the outpost.
    from_file: Annotated[str, Field(pattern=r"^/.+$", max_length=1024)]


class OutpostEndpoint(Strict):
    """One MCP server the outpost may call, under the alias a connector names (ADR 0149 §1)."""

    url: Annotated[str, Field(pattern=r"^https?://[^\s@?#]+$", max_length=2000)]
    auth: OutpostEndpointAuth | None = None
    #: The tools a call may name; a tool outside them is refused `tool_not_allowed`.
    tools: Annotated[list[ToolPattern], Field(max_length=256)]
    #: `deny` unless said otherwise. Whether a tool writes is read from the endpoint's own server
    #: (`readOnlyHint`), never from the call (ADR 0147 §6).
    writes: Literal["deny", "allow"] = "deny"
    timeout_s: Annotated[float, Field(ge=1, le=MAX_LEASE_S)] = 30
    #: The most a call's arguments, or its result, may weigh.
    max_payload_kb: Annotated[int, Field(ge=1, le=1024)] = 256


class OutpostAuditTarget(Strict):
    #: An absolute path; the daemon appends one JSON line per event (`OutpostAuditLine`).
    file: Annotated[str, Field(pattern=r"^/.+$", max_length=1024)]


class OutpostPolicy(Strict):
    """The local policy file of an outpost: what its daemon checks alone, from a file the control
    plane cannot write (ADR 0147 §6). Nothing in it names a project, an agent or a version: those are
    names the control plane asserts, and a rule on them would stop nobody who holds it."""

    #: The door's origin: scheme and host, no path. Every connection the outpost opens goes there.
    control_plane: Annotated[str, Field(pattern=r"^https?://[^\s/@?#]+$", max_length=255)]
    #: The door's Ed25519 keys this outpost obeys: a call signed by no key of this list is not made
    #: (ADR 0148 §13). The file is the only place the set lives; nothing the door sends changes it.
    door_keys: Annotated[list[PublicKey], Field(min_length=1, max_length=MAX_DOOR_KEYS)]
    endpoints: Annotated[dict[EndpointAlias, OutpostEndpoint], Field(max_length=64)]
    #: Calls in progress at once, all endpoints together.
    concurrency: Annotated[int, Field(ge=1, le=MAX_CONCURRENCY)] = 4
    audit: OutpostAuditTarget

    @field_validator("door_keys")
    @classmethod
    def _a_key_is_pinned_once(cls, keys: list[str]) -> list[str]:
        if len(set(keys)) != len(keys):
            raise ValueError("a door key is pinned once")
        return keys


# ───────────────────────── a call, once the envelope is open ─────────────────────────


class OutpostAssertedNames(Lenient):
    """Who the platform says a call is made for: the claims the gateway route signs (ADR 0149 §3).

    They are assertions of the control plane. An outpost may write them to its log; it decides
    nothing on them (ADR 0147 §6). A name this version does not know is ignored."""

    sub: AssertedName | None = None
    org: AssertedName | None = None
    org_id: AssertedName | None = None
    purpose: AssertedName | None = None
    project: AssertedName | None = None
    run: AssertedName | None = None
    work_item: AssertedName | None = None
    agent: AssertedName | None = None
    initiator: AssertedName | None = None
    action: AssertedName | None = None
    connector: AssertedName | None = None
    approved_by: Annotated[list[AssertedName], Field(max_length=16)] = Field(default_factory=list)


class OutpostLoggedNames(OutpostAssertedNames):
    """The asserted names as an audit line keeps them: the ones this contract lists, no other."""

    model_config = ConfigDict(extra="forbid")


class OutpostCall(Lenient):
    """A call as the daemon sees it once it has opened the envelope with its sealing key.

    Only `id` and `kind` are common to every kind, so that a daemon can refuse a kind it does not
    know (`unsupported_kind`) instead of failing on it. The call says nothing of whether the tool
    writes: the outpost reads that from its own server (ADR 0147 §6)."""

    #: The call's id: also the envelope's, and the associated data it was sealed under.
    id: OutpostId
    kind: CallKindName
    endpoint: EndpointAlias | None = None
    tool: ToolName | None = None
    arguments: dict[str, Any] | None = None
    #: The door takes no answer after it, and the outpost makes no call after it. Nothing a daemon
    #: sends moves it. A daemon judges it by its own clock: `server_time` is not signed, and may bring
    #: a deadline closer, never push it away.
    deadline: datetime | None = None
    #: The X25519 key the answer is sealed to. It travels inside the envelope, where no proxy can
    #: rewrite it (ADR 0148 §5).
    answer_key: PublicKey | None = None
    asserted: OutpostAssertedNames = Field(default_factory=OutpostAssertedNames)

    @property
    def known_kind(self) -> OutpostCallKind | None:
        """The kind, when protocol 1 knows it; `None` is refused `unsupported_kind`."""
        return OutpostCallKind(self.kind) if self.kind in set(OutpostCallKind) else None

    @model_validator(mode="after")
    def _a_known_kind_is_complete(self) -> Self:
        kind = self.known_kind
        if kind is None:
            return self
        needed = ["endpoint", "deadline", "answer_key"]
        unwanted: list[str] = []
        if kind is OutpostCallKind.CALL_TOOL:
            needed += ["tool", "arguments"]
        else:
            unwanted = ["tool", "arguments"]
        missing = [name for name in needed if getattr(self, name) is None]
        if missing:
            raise ValueError(f"a `{kind.value}` call needs {', '.join(missing)}")
        extra = [name for name in unwanted if getattr(self, name) is not None]
        if extra:
            raise ValueError(f"a `{kind.value}` call carries no {', '.join(extra)}")
        return self


# ───────────────────────── an answer, before it is sealed ─────────────────────────


class OutpostTool(Strict):
    """A tool the endpoint's server lists and the outpost's policy lets through: what the MCP client
    reads of a `tools/list` entry (`McpTool`)."""

    name: ToolName
    description: Annotated[str, Field(max_length=8192)] = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    #: The server's `annotations.readOnlyHint`; without a declaration a tool is taken for a write.
    read_only: bool = False
    output_schema: dict[str, Any] | None = None


class OutpostServerError(Strict):
    """The JSON-RPC error the endpoint's server answered."""

    message: Annotated[str, Field(max_length=2000)]
    code: int | None = None


class OutpostAnswer(Strict):
    """What a daemon reports of a call it took to its server, before it seals it to `answer_key`."""

    id: OutpostId
    outcome: OutpostAnswerOutcome
    #: `call_tool`, outcome `result`: the server's result as it answered it (`content`,
    #: `structuredContent`, `isError`), untouched.
    result: dict[str, Any] | None = None
    #: `list_tools`, outcome `result`.
    tools: Annotated[list[OutpostTool], Field(max_length=1024)] | None = None
    #: Outcome `error`.
    error: OutpostServerError | None = None
    #: What the outpost read from its own server: the tool does not declare itself read-only. Null for
    #: a kind that calls no tool (`list_tools`), and when the outpost did not get that far.
    write: bool | None = None
    #: The answer comes from the daemon's journal: the server was not called again (ADR 0149 §5).
    replayed: bool = False
    duration_ms: Annotated[int, Field(ge=0)] | None = None

    @model_validator(mode="after")
    def _the_outcome_says_what_it_carries(self) -> Self:
        carried = {name for name in ("result", "tools", "error") if getattr(self, name) is not None}
        if self.outcome is OutpostAnswerOutcome.RESULT:
            if carried not in ({"result"}, {"tools"}):
                raise ValueError("outcome `result` carries `result` or `tools`, one of them")
        elif self.outcome is OutpostAnswerOutcome.ERROR:
            if carried != {"error"}:
                raise ValueError("outcome `error` carries `error` and nothing else")
        elif carried:
            raise ValueError(f"outcome `{self.outcome.value}` carries no {', '.join(sorted(carried))}")
        if self.tools is not None and self.write is not None:
            raise ValueError("a tool listing calls no tool: its `write` is null")
        return self


class OutpostRefusal(Strict):
    """Why an outpost did not execute a call: the body of the door's `refuse` verb. A code and
    nothing else, so that a refusal names a class and never the rule (ADR 0147 §10)."""

    code: OutpostRefusalCode


# ───────────────────────── the local audit log ─────────────────────────


#: What an audit line cannot say of a call refused for its signature: no pinned key verified it, and
#: its envelope was not opened.
UNOPENED: Final = ("signed_by", "kind", "endpoint", "tool", "arguments_sha256", "write")


class OutpostAuditLine(Strict):
    """One line of the outpost's own audit log (ADR 0147 §8): the operator's record, independent of
    the platform's, so that the two can be compared. It holds digests, never arguments or results."""

    at: datetime
    event: OutpostAuditEvent
    call_id: OutpostId
    #: The pinned key that verified the call's signature; null when no pinned key did.
    signed_by: PublicKey | None = None
    kind: CallKindName | None = None
    endpoint: EndpointAlias | None = None
    tool: ToolName | None = None
    #: `arguments_sha256` of the call's arguments: the digest the platform's `tool.called` event keeps.
    arguments_sha256: Sha256 | None = None
    write: bool | None = None
    #: `executed`: what was reported. `replayed`: what the journal held for the call, null while the
    #: call it repeats is still under way and has no answer to replay yet.
    outcome: OutpostAnswerOutcome | None = None
    #: `refused`: why.
    refusal: OutpostRefusalCode | None = None
    duration_ms: Annotated[int, Field(ge=0)] | None = None
    asserted: OutpostLoggedNames = Field(default_factory=OutpostLoggedNames)

    @model_validator(mode="after")
    def _the_event_says_what_it_carries(self) -> Self:
        if self.event is OutpostAuditEvent.REFUSED:
            if self.refusal is None or self.outcome is not None:
                raise ValueError("a `refused` line carries `refusal` and no `outcome`")
        elif self.event is OutpostAuditEvent.RECEIVED:
            if self.refusal is not None or self.outcome is not None:
                raise ValueError("a `received` line carries no `outcome` and no `refusal`")
        elif self.event is OutpostAuditEvent.EXECUTED:
            if self.outcome is None or self.refusal is not None:
                raise ValueError("an `executed` line carries `outcome` and no `refusal`")
        elif self.refusal is not None:
            raise ValueError("a `replayed` line carries no `refusal`")
        if self.refusal is OutpostRefusalCode.SIGNATURE_REFUSED:
            opened = [name for name in UNOPENED if getattr(self, name) is not None]
            if opened:
                raise ValueError(
                    f"a call refused for its signature was not opened: no {', '.join(opened)} is known"
                )
        return self


# ───────────────────────── the door's four verbs ─────────────────────────


class OutpostProtocolWindow(Lenient):
    """The protocol versions the door serves; a daemon below `minimum` is refused, and says so."""

    current: Protocol
    minimum: Protocol


class OutpostEnrolmentRequest(Strict):
    """`POST /outposts/v1/enrol`: an enrolment token and the public halves of the keys the instance
    made on the spot (ADR 0147 §2, §4). The request is signed with the key it enrols."""

    protocol: Protocol
    token: Annotated[str, Field(pattern=r"^[A-Za-z0-9._~-]{16,256}$")]
    #: Ed25519: verifies every later request of this instance.
    signing_key: PublicKey
    #: X25519: the calls this instance claims are sealed to it.
    sealing_key: PublicKey
    daemon_version: DaemonVersion
    capabilities: Annotated[list[AnnouncedCapability], Field(max_length=4)]
    #: Facts about the instance, for the person who approves it: bounded, stored as data, never
    #: interpreted (ADR 0148 §9).
    facts: Annotated[
        dict[
            Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")],
            Annotated[str, Field(max_length=256)],
        ],
        Field(max_length=16),
    ] = Field(default_factory=dict)


class OutpostEnrolmentAnswer(Lenient):
    """The door's answer to an enrolment: a pending instance, handed nothing, and the fingerprint a
    person compares before approving it."""

    instance_id: OutpostId
    organisation: Annotated[str, Field(min_length=1, max_length=128)]
    outpost: OutpostSlug
    status: Literal["pending"]
    fingerprint: Fingerprint
    protocol: OutpostProtocolWindow
    server_time: datetime


class OutpostPollRequest(Strict):
    """`POST /outposts/v1/poll`: how long the instance waits, what it can take and what it holds."""

    protocol: Protocol
    daemon_version: DaemonVersion
    wait_s: Annotated[int, Field(ge=0, le=MAX_WAIT_S)] = MAX_WAIT_S
    #: How many more calls it can take now; the door hands over no more. 0 takes none.
    capacity: Annotated[int, Field(ge=0, le=MAX_CONCURRENCY)]
    capabilities: Annotated[list[AnnouncedCapability], Field(max_length=4)]
    #: The aliases its policy file serves: names only (ADR 0147 §7, §10).
    endpoints: Annotated[list[EndpointAlias], Field(max_length=64)] = Field(default_factory=list)
    #: The calls it holds and has not answered.
    running: Annotated[list[OutpostId], Field(max_length=MAX_RUNNING)] = Field(default_factory=list)


class OutpostSealedCall(Lenient):
    """A call as it crosses the door: its id, everything else sealed to the claiming instance, and the
    door's signature over both (ADR 0148 §13).

    The door always signs. The model still reads a call without a signature, or with one that is
    not a JWS, so that a daemon can refuse that one call by its id (`signature_refused`) instead of
    losing the whole answer; it never opens such an envelope."""

    id: OutpostId
    sealed: Annotated[str, Field(pattern=SEALED_PATTERN, max_length=MAX_SEALED_CALL_BYTES)]
    signature: Annotated[str, Field(max_length=2048)] | None = None


class OutpostPollAnswer(Lenient):
    """The door's answer to a poll. One connection per instance carries every lane."""

    protocol: OutpostProtocolWindow
    server_time: datetime
    #: The outpost is paused: nothing is handed over; what the instance holds may finish.
    stop: bool = False
    #: At most `capacity` calls, none heavier than `MAX_SEALED_CALL_BYTES`.
    calls: Annotated[list[OutpostSealedCall], Field(max_length=MAX_CONCURRENCY)] = Field(default_factory=list)
    #: Calls among `running` the door waits for no more: their answer would be refused `lease_lost`. A
    #: call not yet started is refused `cancelled`; a read in flight is abandoned and answered
    #: `unanswered`; a write that has reached its server is never interrupted: it finishes.
    cancel: Annotated[list[OutpostId], Field(max_length=MAX_RUNNING)] = Field(default_factory=list)


class OutpostSealedAnswer(Strict):
    """`POST /outposts/v1/calls/{id}/answer`: an `OutpostAnswer` sealed to the call's `answer_key`."""

    sealed: Annotated[str, Field(pattern=SEALED_PATTERN)]


class OutpostCallAck(Lenient):
    """What the door recorded for a call: the first of an answer and a refusal, once."""

    id: OutpostId
    recorded: Literal["answer", "refusal"]


class OutpostDoorProblem(Lenient):
    """A problem of the door (RFC 9457). `code` is one of `OutpostDoorCode` in protocol 1; a daemon
    that meets a code it does not know goes by the HTTP status."""

    type: str = "about:blank"
    title: str
    status: int
    code: str
    #: The door's clock: a daemon whose own drifts learns it from any refusal.
    server_time: datetime
    detail: str | None = None
    instance: str | None = None
    errors: list[dict[str, Any]] | None = None
    retry_after: int | None = None
    #: On `protocol_unsupported`: the window the door serves.
    protocol: OutpostProtocolWindow | None = None

    @property
    def known_code(self) -> OutpostDoorCode | None:
        return OutpostDoorCode(self.code) if self.code in set(OutpostDoorCode) else None


# ───────────────────────── what a signature binds, what a digest covers ─────────────────────────


def _unpadded(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def fingerprint_of(signing_key: str) -> str:
    """The fingerprint a daemon prints and a person compares in the console (ADR 0147 §4): `SHA256:`
    and the unpadded base64url SHA-256 of the instance's raw Ed25519 public key."""
    raw = base64.urlsafe_b64decode(signing_key + "=" * (-len(signing_key) % 4))
    if len(raw) != 32:
        raise ValueError("a signing key is 32 bytes, base64url without padding")
    return "SHA256:" + _unpadded(hashlib.sha256(raw).digest())


def body_sha256(body: bytes) -> str:
    """The `body_sha256` claim: the SHA-256 of the request's body exactly as sent, lowercase hex."""
    return hashlib.sha256(body).hexdigest()


def request_signature_claims(
    *,
    method: str,
    url: str,
    body: bytes,
    iat: int,
    nonce: str | None = None,
    seq: int | None = None,
) -> dict[str, Any]:
    """The claims of a request's signature, the instance's (`Outpost-Signature`, ADR 0148 §4).

    `url` is the request's URL without query or fragment: the policy file's `control_plane` followed
    by the verb's path. `nonce` and `seq` are left out of the enrolment's signature only: no nonce
    has been issued and no row holds a sequence yet."""
    claims: dict[str, Any] = {
        "htm": method.upper(),
        "htu": url,
        "body_sha256": body_sha256(body),
        "iat": iat,
    }
    if nonce is not None:
        claims["nonce"] = nonce
    if seq is not None:
        claims["seq"] = seq
    return claims


def request_signature_header(instance_id: str | None = None) -> dict[str, str]:
    """The protected header of a request's signature; `kid` names the instance once it has an id."""
    header = {"alg": SIGNATURE_ALGORITHM, "typ": REQUEST_SIGNATURE_TYPE}
    if instance_id is not None:
        header["kid"] = instance_id
    return header


def sealed_sha256(sealed: str) -> str:
    """The `sealed_sha256` claim: the SHA-256 of the envelope exactly as carried (the ASCII bytes of
    the `sealed` string), lowercase hex."""
    return hashlib.sha256(sealed.encode("ascii")).hexdigest()


def call_signature_claims(*, call_id: str, instance_id: str, sealed: str) -> dict[str, str]:
    """The claims of a call's signature, the door's (ADR 0148 §13): this call, for this instance, in
    this envelope. A daemon accepts a signature whose claims are exactly these, and no other."""
    return {"call": call_id, "instance": instance_id, "sealed_sha256": sealed_sha256(sealed)}


def call_signature_header(door_key: str) -> dict[str, str]:
    """The protected header of a call's signature: `kid` is the door's signing key itself, which the
    daemon looks for among the keys its policy file pins before it verifies anything."""
    return {"alg": SIGNATURE_ALGORITHM, "typ": CALL_SIGNATURE_TYPE, "kid": door_key}


def max_poll_answer_bytes(capacity: int) -> int:
    """The most a poll's answer weighs for a daemon that asked for `capacity` calls: what a daemon may
    bound its reading to. No call the door hands over is heavier than `MAX_SEALED_CALL_BYTES`, and
    everything else in the answer fits in `MAX_POLL_ANSWER_REST_BYTES`."""
    if not 0 <= capacity <= MAX_CONCURRENCY:
        raise ValueError(f"a poll asks for 0 to {MAX_CONCURRENCY} calls")
    return MAX_POLL_ANSWER_REST_BYTES + capacity * MAX_SEALED_CALL_BYTES


def next_sequence(last: int | None, now_ms: int) -> int:
    """The `seq` of a daemon's next request: its clock in milliseconds, and never below its last
    sequence plus one. A daemon that lost its state file (`last` is `None`) is therefore not left
    behind the sequence the door keeps: its clock is already past every number it ever sent."""
    sequence = now_ms if last is None else max(now_ms, last + 1)
    if not 1 <= sequence <= MAX_SEQUENCE:
        raise ValueError("a sequence is a whole number from 1 to 2^53 - 1")
    return sequence


def arguments_sha256(arguments: dict[str, Any]) -> str:
    """The digest of a call's arguments, as the platform's `tool.called` event keeps it: the SHA-256
    of their canonical JSON (sorted keys, compact separators, UTF-8), lowercase hex. The outpost's
    audit line and the platform's record of the same call carry the same digest."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
