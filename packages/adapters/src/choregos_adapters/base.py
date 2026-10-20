# SPDX-License-Identifier: Apache-2.0
"""Interfaces des adaptateurs (docs/plan/01 §1.9).

Chaque `Protocol` a une implémentation réelle et un `Fake*` en mémoire. Les flux
travaillent contre les fakes tant que le connecteur réel n'existe pas : c'est ce qui
permet aux treize flux d'avancer en parallèle sans se bloquer.
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

from .oci.builder import BuildRequest, BuildResult
from .oci.signatures import SignatureVerdict


@runtime_checkable
class TrackerAdapter(Protocol):
    """Le tracker est l'interface humaine : Choregos y reflète l'état, le coût et les preuves.

    `owns_items` dit **qui tient le ticket**. Chez GitHub ou Jira, le ticket existe dehors :
    Choregos le relit, le reflète, et découvre par `list_candidates` ce qui lui est confié.
    Avec le tracker interne, il n'y a pas de dehors — la base de Choregos EST le tracker.
    Sans cette distinction, un ticket créé par la plateforme (un finding promu, une demande
    saisie dans le front) n'était jamais découvert : `list_candidates` rendait une liste
    vide, et le ticket restait en `inbox` pour toujours. Vu sur le banc du 2026-09-23.

    `create_item` is also what files a person's request from the board or MCP in an external
    tracker (ADR 0059): the work item is then recorded under the key it returns.
    """

    #: Faux quand la base de Choregos est la source (cf. `tracker: internal`).
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
    """Le SCM : branches, PR, checks, merge queue. Les tokens sont mintés par run."""

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


#: Ce qu'un exécuteur peut savoir faire en plus de démarrer, observer et arrêter.
#:
#: Ce vocabulaire est la **couture** par laquelle un bac à sable plus capable entrera un
#: jour — celui de Google (Agent Substrate, gVisor) ou un autre. Il est posé maintenant,
#: alors qu'un seul exécuteur en remplit un seul, pour que ce jour-là rien d'autre ne
#: bouge : l'orchestrateur demandera ce que le runtime sait faire au lieu de le supposer.
#:
#: - `queue`    : sait faire attendre un run SANS pod (plafond de simultanéité).
#: - `suspend`  : sait arrêter un run EN COURS sans perdre son travail.
#: - `resume`   : sait le reprendre là où il s'était arrêté.
#: - `renew`    : sait remplacer le jeton d'un run QUI N'A PAS ENCORE DÉMARRÉ. Indispensable
#:                dès qu'il y a une file : un jeton minté à la mise en file expire pendant
#:                l'attente, et l'agent démarre pour recevoir « Signature has expired ».
#: - `snapshot` : every run gets a sandbox made from a clean template and never used before
#:                (ADR 0016, ADR 0053) — what makes a pool of warm runners acceptable
#:                (ADR 0013), because no run inherits another's filesystem or credentials.
#:                A property of `start`, like `queue`: no method. Freezing a RUNNING sandbox
#:                and restoring it is not what this word promises.
CAPACITES_EXECUTEUR: frozenset[str] = frozenset({"queue", "renew", "suspend", "resume", "snapshot"})


@runtime_checkable
class Executor(Protocol):
    """Ce qui fait tourner un run : PipelineRun Tekton, Job K8s, conteneur local, ACA Job."""

    #: Ce que CET exécuteur sait faire, pris dans `CAPACITES_EXECUTEUR`. Déclarer une
    #: capacité qu'on n'implémente pas est refusé par la conformité : une capacité
    #: annoncée et absente est pire qu'absente, l'appelant s'y fie.
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
    """Un backend ACP : comment le lancer et comment lui donner son modèle."""

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
#: - `pricing`: the catalogue carries input/output prices.
GATEWAY_CAPABILITIES: frozenset[str] = frozenset(
    {"list_models", "virtual_keys", "hard_budget", "spend_readback", "teams", "pricing"}
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
class Notifier(Protocol):
    async def send(self, channel: str, message: Message) -> None: ...


__all__ = [
    "GATEWAY_CAPABILITIES",
    "AgentBackend",
    "CdAdapter",
    "CiAdapter",
    "Executor",
    "GatewayAdapter",
    "LlmGateway",
    "MemoryAdapter",
    "Notifier",
    "ScmAdapter",
    "TrackerAdapter",
]
