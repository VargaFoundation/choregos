# SPDX-License-Identifier: Apache-2.0
"""Adapter interfaces (docs/plan/01 §1.9).

Every `Protocol` has a real implementation and an in-memory `Fake*`. The streams work against
the fakes until the real connector exists: that is what lets the thirteen streams move in
parallel without blocking each other.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from typing import Any, Literal, Protocol, runtime_checkable

from choregos_contracts import ContextPack, InboundEvent, ProjectConfig
from choregos_core.domain import (
    Change,
    CiStatus,
    DiffSummary,
    ExecRef,
    ExecStatus,
    Fact,
    GatewayModel,
    Health,
    LaunchContext,
    LaunchSpec,
    Memory,
    Message,
    NewItem,
    PromotionRef,
    Provenance,
    PrRef,
    PrState,
    RolloutState,
    Spend,
    StageJobSpec,
    TrackerStateMapping,
    VirtualKey,
    Window,
    WorkItemData,
)

from .decisions import DecisionCall, DecisionReply
from .oci.builder import BuildRequest, BuildResult
from .oci.signatures import SignatureVerdict


@runtime_checkable
class TrackerAdapter(Protocol):
    """The tracker is the human interface: Choregos reflects the state, the cost and the proofs in it.

    `owns_items` says **who holds the ticket**. With GitHub or Jira, the ticket lives outside:
    Choregos reads it back, reflects it, and discovers through `list_candidates` what is assigned
    to it. With the internal tracker there is no outside: Choregos' database IS the tracker.
    Without this distinction, a ticket created by the platform (a promoted finding, a request
    typed in the front end) was never discovered: `list_candidates` returned an empty list, and
    the ticket stayed in `inbox` forever. Seen on the bench of 2026-09-23.

    `create_item` is also what files a person's request from the board or MCP in an external
    tracker (ADR 0059): the work item is then recorded under the key it returns.
    """

    #: False when Choregos' database is the source (see `tracker: internal`).
    owns_items: bool

    async def fetch_item(self, key: str) -> WorkItemData: ...
    async def create_item(self, data: NewItem) -> str: ...
    async def set_state(self, key: str, state_mapping: TrackerStateMapping) -> None: ...
    async def upsert_status_comment(self, key: str, markdown: str, marker: str) -> None: ...
    #: Closes a delivered work item (S22-28). Idempotent: closing a closed item does nothing.
    async def close_item(self, key: str) -> None: ...
    async def comment(self, key: str, markdown: str) -> str: ...
    async def link(self, a: str, b: str, relation: Literal["origin", "duplicate", "blocks"]) -> None: ...
    async def set_fields(self, key: str, fields: dict[str, Any]) -> None: ...
    async def list_candidates(self, project: ProjectConfig) -> list[str]: ...
    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]: ...
    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool: ...


@runtime_checkable
class ScmAdapter(Protocol):
    """The SCM: branches, PRs, checks, merge queue. The tokens are minted per run."""

    async def mint_token(self, repo: str, ttl_s: int, scopes: list[str]) -> str: ...
    async def ensure_branch(self, repo: str, name: str, base: str) -> None: ...
    #: Whether the branch exists on the repository (S22-46): no pull request is asked for without one.
    async def branch_exists(self, repo: str, name: str) -> bool: ...
    async def commit_files(self, repo: str, branch: str, files: dict[str, str], message: str) -> str: ...
    async def open_pr(self, repo: str, head: str, base: str, title: str, body: str, draft: bool) -> PrRef: ...
    async def update_pr(self, ref: PrRef, body: str | None, draft: bool | None) -> None: ...
    async def comment_pr(self, ref: PrRef, body: str) -> str: ...
    async def get_pr(self, ref: PrRef) -> PrState: ...
    async def request_review(self, ref: PrRef, reviewers: list[str]) -> None: ...
    async def enqueue_merge(self, ref: PrRef) -> None: ...
    async def compare(self, repo: str, base: str, head: str) -> DiffSummary: ...
    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]: ...


@runtime_checkable
class CiAdapter(Protocol):
    async def status_for(self, repo: str, sha: str) -> CiStatus: ...
    async def logs(self, run_ref: str, tail: int = 500) -> str: ...
    async def trigger(self, repo: str, ref: str, pipeline: str) -> str: ...
    def parse_event(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]: ...


@runtime_checkable
class CdAdapter(Protocol):
    async def current_revision(self, app: str) -> str: ...
    async def promote(self, env: str, changes: list[Change], release: str) -> PromotionRef: ...
    async def health(self, app: str) -> Health: ...
    async def rollout_status(self, app: str) -> RolloutState: ...
    async def abort_rollout(self, app: str) -> None: ...
    async def set_sync_window(self, app: str, windows: list[Window]) -> None: ...


#: What an executor may be able to do besides starting, observing and stopping.
#:
#: This vocabulary is the **seam** through which a more capable sandbox will enter one day, such
#: as Google's (Agent Substrate, gVisor) or another. It is laid down now, while a single executor
#: fills a single one of them, so that nothing else moves on that day: the orchestrator will ask
#: what the runtime can do instead of assuming it.
#:
#: - `queue`    : can make a run wait WITHOUT a pod (concurrency ceiling).
#: - `suspend`  : can stop a RUNNING run without losing its work.
#: - `resume`   : can resume it where it stopped.
#: - `renew`    : can replace the token of a run THAT HAS NOT STARTED YET. Indispensable as soon
#:                as there is a queue: a token minted when queued expires during the wait, and
#:                the agent starts only to receive "Signature has expired".
#: - `snapshot` : every run gets a sandbox made from a clean template and never used before
#:                (ADR 0016, ADR 0053) — what makes a pool of warm runners acceptable
#:                (ADR 0013), because no run inherits another's filesystem or credentials.
#:                A property of `start`, like `queue`: no method. Freezing a RUNNING sandbox
#:                and restoring it is not what this word promises.
EXECUTOR_CAPABILITIES: frozenset[str] = frozenset({"queue", "renew", "suspend", "resume", "snapshot"})


@runtime_checkable
class Executor(Protocol):
    """What runs a run: a Tekton PipelineRun, a K8s Job, a local container, an ACA Job."""

    #: What THIS executor can do, taken from `EXECUTOR_CAPABILITIES`. Declaring a capability that
    #: is not implemented is refused by conformance: a capability announced and absent is worse
    #: than absent, the caller relies on it.
    capabilities: frozenset[str]

    async def start(self, spec: StageJobSpec) -> ExecRef: ...
    async def status(self, ref: ExecRef) -> ExecStatus: ...
    def logs(self, ref: ExecRef) -> AsyncIterator[str]: ...
    async def cancel(self, ref: ExecRef) -> None: ...


@runtime_checkable
class RegistryClient(Protocol):
    """An OCI registry, read-only: the digest a reference names now (ADR 0080). Nothing is pulled."""

    async def resolve(self, reference: str) -> str: ...


@runtime_checkable
class ImageVerifier(Protocol):
    """Whether a pinned image (`ref@sha256:…`) carries a signature the operator trusts (ADR 0080)."""

    async def verify(self, pinned: str) -> SignatureVerdict: ...


@runtime_checkable
class ImageBuilder(Protocol):
    """Builds a hosted agent's zip into an image, once per build key (ADR 0080 §9)."""

    name: str

    async def build(self, request: BuildRequest) -> BuildResult: ...


@runtime_checkable
class AgentBackend(Protocol):
    """An ACP backend: how to launch it and how to give it its model."""

    name: str
    capabilities: frozenset[str]

    def launch_spec(self, ctx: LaunchContext) -> LaunchSpec: ...
    def model_env(self, model: Any) -> dict[str, str]: ...


@runtime_checkable
class MemoryAdapter(Protocol):
    async def context_pack(
        self, project: str, query: str, paths: list[str], budget_tokens: int, kinds: list[str] | None = None
    ) -> ContextPack: ...
    async def write_fact(self, project: str, fact: Fact) -> str: ...
    async def propose_fact(self, project: str, fact: Fact, provenance: Provenance) -> str: ...
    async def ingest_events(self, project: str, events: list[dict[str, Any]]) -> None: ...
    async def search(self, project: str, query: str, k: int) -> list[Memory]: ...


#: What a gateway can do. A gateway declares the subset it really implements, and a conformance
#: test refuses a declaration its behaviour contradicts (same discipline as `Executor.capabilities`).
#:
#: - `list_models`: discovers the models it routes;
#: - `virtual_keys`: mints one key per run, limited to the models asked for;
#: - `hard_budget`: the key's budget is a cap enforced at the source;
#: - `spend_readback`: the spend of a key can be read back (cost measured at the source);
#: - `teams`: a project's day budget is held by the gateway;
#: - `pricing`: the catalogue carries input/output prices;
#: - `realtime`: relays the realtime WebSocket API (`/realtime?model=`) under a virtual key, which a
#:   voice agent needs (ADR 0080): one is refused at publication on a gateway that does not;
#: - `llm_guard`: masks secrets in what a marked key sends the provider and in the answer
#:   (ADR 0084): a policy with `sandbox.llm_security_analyzer` is refused on a gateway without it;
#: - `systemone`: serves decision models on `POST /v1/systemone` under a virtual key, with the same
#:   budgets and spend logs as chat models (LiteLLM >= 1.104.2), which a decision step needs
#:   (ADR 0121): one is refused for a project whose gateway does not.
GATEWAY_CAPABILITIES: frozenset[str] = frozenset(
    {
        "list_models",
        "virtual_keys",
        "hard_budget",
        "spend_readback",
        "teams",
        "pricing",
        "realtime",
        "llm_guard",
        "systemone",
    }
)


def llm_guard_refusal(gateway: Any) -> str | None:
    """Why `gateway` cannot hold the LLM guard a policy asks for (ADR 0084), or None when it can.

    The guard lives in the gateway, not in the agent: a gateway without one (`direct`,
    `openai-compatible`) would carry the key's mark to nobody, so the key is refused, never minted
    unguarded.
    """
    if "llm_guard" in getattr(gateway, "capabilities", frozenset()):
        return None
    return (
        "the policy masks secrets on the LLM path (`sandbox.llm_security_analyzer`) and the "
        f"`{type(gateway).__name__}` gateway declares no `llm_guard`: use the LiteLLM gateway, "
        "or turn the analyzer off"
    )


def decision_route_refusal(gateway: Any) -> str | None:
    """Why `gateway` cannot serve a decision step (ADR 0121), or None when it can.

    A decision step calls its model through the gateway, never directly, so that its cost is counted
    and capped at the source: a gateway without the decision route has nowhere to send it.
    """
    if "systemone" in getattr(gateway, "capabilities", frozenset()):
        return None
    return (
        "a decision step calls its model on the gateway's `/v1/systemone` route and the "
        f"`{type(gateway).__name__}` gateway declares no `systemone`: use the LiteLLM gateway"
    )


@runtime_checkable
class GatewayAdapter(Protocol):
    """The model gateway: one virtual key per run, the cost counted at the source.

    Documented as `LlmGateway`: nothing in the protocol is specific to LiteLLM."""

    capabilities: frozenset[str]

    async def mint_key(
        self,
        metadata: dict[str, Any],
        budget_usd: float,
        ttl_s: int,
        models: list[str],
        team_id: str | None = None,
    ) -> VirtualKey: ...
    async def ensure_team(self, team_id: str, budget_usd: float, duration: str = "1d") -> str: ...
    async def spend(self, key_id: str) -> Spend: ...
    async def revoke(self, key_id: str) -> None: ...
    async def list_models(self) -> list[GatewayModel]: ...
    async def test(self) -> dict[str, Any]: ...


LlmGateway = GatewayAdapter


@runtime_checkable
class DecisionClient(Protocol):
    """Asks a decision model typed questions through the gateway's `/v1/systemone` (ADR 0121 §2).

    Never a model directly: `call.base_url` is the project's gateway and `call.key` the virtual key
    minted for the step, so the gateway counts and caps the cost. Raises `InvalidDecisionError` for an
    answer that cannot be used as it is and `DecisionUnreachableError` when there is no answer
    (`choregos_adapters.decisions`, which also holds the wire format and its sources)."""

    async def decide(self, call: DecisionCall) -> DecisionReply: ...


class RealtimeClosed(Exception):  # noqa: N818 - the end of a realtime link, not a failure
    """The other end closed the realtime link."""

    def __init__(self, code: int = 1000, reason: str = "") -> None:
        super().__init__(f"closed ({code}) {reason}".strip())
        self.code = code
        self.reason = reason


@runtime_checkable
class RealtimeLink(Protocol):
    """One open realtime WebSocket: JSON text events, both ways."""

    async def send(self, message: str) -> None: ...
    async def recv(self) -> str: ...
    async def close(self) -> None: ...


@runtime_checkable
class RealtimeConnector(Protocol):
    """Opens the gateway's realtime WebSocket (ADR 0080 §10): a voice session's upstream."""

    async def connect(self, url: str, headers: dict[str, str]) -> RealtimeLink: ...


@runtime_checkable
class Notifier(Protocol):
    async def send(self, channel: str, message: Message) -> None: ...


__all__ = [
    "EXECUTOR_CAPABILITIES",
    "GATEWAY_CAPABILITIES",
    "AgentBackend",
    "CdAdapter",
    "CiAdapter",
    "DecisionClient",
    "Executor",
    "GatewayAdapter",
    "LlmGateway",
    "MemoryAdapter",
    "Notifier",
    "RealtimeClosed",
    "RealtimeConnector",
    "RealtimeLink",
    "ScmAdapter",
    "TrackerAdapter",
    "decision_route_refusal",
    "llm_guard_refusal",
]
