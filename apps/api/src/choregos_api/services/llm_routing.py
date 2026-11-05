# SPDX-License-Identifier: Apache-2.0
"""A small model chooses the workflow of a work item that names none (ADR 0116).

The project's routing strategy decides when it is asked: never (`labels`), when no rule matched
(`rules_then_llm`), or instead of the rules (`llm`). It reads the item — title, body, labels, type —
and, for each active workflow, its name, its description and the hint written in the console. It
answers one workflow among them or none; anything else (an unknown name, a timeout, an error, a
model the catalogue refuses, no gateway key) leaves the work item to the default. The birth never
fails because of the model.

Two kinds of model can answer (ADR 0121). When the organisation's catalogue enables an entry for both
`route` and `decide`, that decision model is asked one `choice` question on the gateway's
`/v1/systemone` and answers a workflow with a probability; below the project's `min_confidence` the
default takes the item. Otherwise the `cheap` tier is asked on `/v1/chat/completions` for one JSON
object, and its answer carries no confidence.

It is asked once per birth (ADR 0008): the decision is kept in the database session by a digest of
what was asked, so checking a birth and creating the work item ask once. Its spend is one `route`
line in the cost ledger, in the transaction that creates the work item (ADR 0004, 0092).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..db.models import Project, WorkflowDef
from ..logging import get_logger
from .definitions import workflow_model
from .model_catalogue import load_policy, price_of

logger = get_logger(__name__)

#: Who spent it, in the cost ledger.
ROUTER_AGENT = "workflow-router"
#: The catalogue usage the model must be enabled for (ADR 0060).
ROUTE_USAGE = "route"
#: With `route`, it makes the entry a decision model asked on `/v1/systemone` (ADR 0121).
DECIDE_USAGE = "decide"
#: The one question a decision model is asked; its options are the active workflows.
DECISION_QUESTION = "Which of the project's workflows should handle this work item?"
TIMEOUT_SECONDS = 15.0
MAX_ANSWER_TOKENS = 200
BODY_CHARS = 4000
CHARS_PER_TOKEN = 4
_SESSION_KEY = "choregos.workflow_routing"

Strategy = Literal["labels", "rules_then_llm", "llm"]
STRATEGIES: tuple[str, ...] = ("labels", "rules_then_llm", "llm")

INSTRUCTIONS = """You route a work item to the workflow of its project that should handle it.
You receive the item and the project's workflows, each with its name, its description and a hint.
Answer one JSON object: {"workflow": "<one of the names given>" or null, "reason": "<one sentence>"}.
Answer null when no workflow clearly fits: the item then goes to the project's default workflow.
Never answer a name that is not in the list."""


class RoutingError(RuntimeError):
    """The model could not answer; the work item goes to the default."""


@dataclass(frozen=True, slots=True)
class Candidate:
    name: str
    description: str | None
    hint: str | None


@dataclass(frozen=True, slots=True)
class Item:
    title: str
    body: str | None
    labels: tuple[str, ...]
    item_type: str | None


@dataclass(frozen=True, slots=True)
class Answer:
    workflow: str | None
    reason: str
    input_tokens: int
    output_tokens: int
    #: The probability a decision model gives its choice; a chat model gives none.
    confidence: float | None = None


class WorkflowRouter(Protocol):
    model: str

    async def choose(self, candidates: list[Candidate], item: Item) -> Answer: ...


def prompt_of(candidates: list[Candidate], item: Item) -> str:
    return json.dumps(
        {
            "item": {
                "title": item.title,
                "body": (item.body or "")[:BODY_CHARS],
                "labels": list(item.labels),
                "type": item.item_type,
            },
            "workflows": [
                {"name": c.name, "description": c.description or "", "hint": c.hint or ""} for c in candidates
            ],
        },
        ensure_ascii=False,
    )


_WORD = re.compile(r"[a-z0-9]+")


def _words(text: str, minimum: int) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) >= minimum}


class FakeWorkflowRouter:
    """Deterministic (ADR 0005): the workflow whose name, hint or description shares the most words
    with the item; none on a tie or when nothing is shared."""

    model = "fake/router"

    async def choose(self, candidates: list[Candidate], item: Item) -> Answer:
        said = _words(" ".join([item.title, item.body or "", *item.labels, item.item_type or ""]), 3)
        scores: dict[str, set[str]] = {}
        for c in candidates:
            vocabulary = _words(c.name.replace("-", " "), 3) | _words(
                f"{c.hint or ''} {c.description or ''}", 4
            )
            scores[c.name] = said & vocabulary
        best = max((len(shared) for shared in scores.values()), default=0)
        winners = [name for name, shared in scores.items() if len(shared) == best]
        prompt = prompt_of(candidates, item)
        if best == 0 or len(winners) != 1:
            return Answer(None, "no workflow clearly fits", len(prompt) // CHARS_PER_TOKEN, 10)
        (winner,) = winners
        reason = f"the item speaks of {', '.join(sorted(scores[winner]))}"
        return Answer(winner, reason, len(prompt) // CHARS_PER_TOKEN, 20)


class FakeDecisionRouter:
    """Deterministic (ADR 0005), as a decision model answers: always one workflow, with a probability.

    The workflow sharing the most words with the item, at 0.5 plus 0.1 per shared word (at most 0.95);
    on a tie or when nothing is shared, the first by name at one over the number of candidates."""

    model = "fake/decide"

    async def choose(self, candidates: list[Candidate], item: Item) -> Answer:
        chosen = await FakeWorkflowRouter().choose(candidates, item)
        if chosen.workflow is not None:
            shared = chosen.reason.removeprefix("the item speaks of ").split(", ")
            confidence = min(0.95, 0.5 + 0.1 * len(shared))
            return Answer(chosen.workflow, chosen.reason, chosen.input_tokens, 0, confidence)
        first = min(c.name for c in candidates)
        return Answer(first, "no workflow clearly fits", chosen.input_tokens, 0, 1 / len(candidates))


class GatewayWorkflowRouter:
    """One chat completion on the gateway, not streamed, answering one JSON object."""

    def __init__(
        self,
        base_url: str,
        key: str,
        model: str,
        *,
        timeout: float = TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url, self.key, self.model, self.timeout = base_url.rstrip("/"), key, model, timeout
        self.transport = transport

    async def choose(self, candidates: list[Candidate], item: Item) -> Answer:
        prompt = prompt_of(candidates, item)
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.post(
                    f"{self.base_url}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={
                        "model": self.model,
                        "messages": [
                            {"role": "system", "content": INSTRUCTIONS},
                            {"role": "user", "content": prompt},
                        ],
                        "response_format": {"type": "json_object"},
                        "max_tokens": MAX_ANSWER_TOKENS,
                        "temperature": 0,
                        "metadata": {"choregos_agent": ROUTER_AGENT},
                    },
                )
        except httpx.HTTPError as exc:
            raise RoutingError(f"the gateway did not answer: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise RoutingError(f"the gateway answered {response.status_code}: {response.text[:200]}")
        try:
            body = response.json()
            content = str(body["choices"][0]["message"]["content"])
            answer = dict(json.loads(content))
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RoutingError("the model did not answer one JSON object") from exc
        used = body.get("usage") or {}
        workflow = answer.get("workflow")
        return Answer(
            workflow=str(workflow) if isinstance(workflow, str) and workflow else None,
            reason=str(answer.get("reason") or "")[:300],
            input_tokens=int(used.get("prompt_tokens") or len(INSTRUCTIONS + prompt) // CHARS_PER_TOKEN),
            output_tokens=int(used.get("completion_tokens") or len(content) // CHARS_PER_TOKEN),
        )


def criteria_of(candidates: list[Candidate]) -> dict[str, str | None]:
    """Each workflow's name, with what the decision model reads of it: its description and its hint."""
    return {c.name: " ".join(t for t in (c.description, c.hint) if t) or None for c in candidates}


class DecisionWorkflowRouter:
    """One `choice` question on the gateway's `/v1/systemone` (LiteLLM 1.105.5, ADR 0121): the item is
    the state, the active workflows are the options, and the answer carries its probability. LiteLLM
    translates it for an OpenAI decision model as for a self-hosted one."""

    def __init__(
        self,
        base_url: str,
        key: str,
        model: str,
        *,
        timeout: float = TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url, self.key, self.model, self.timeout = base_url.rstrip("/"), key, model, timeout
        self.transport = transport

    async def choose(self, candidates: list[Candidate], item: Item) -> Answer:
        state = {
            "title": item.title,
            "description": (item.body or "")[:BODY_CHARS],
            "labels": list(item.labels),
            "type": item.item_type,
        }
        question = {"type": "choice", "instructions": DECISION_QUESTION, "criteria": criteria_of(candidates)}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self.transport) as client:
                response = await client.post(
                    f"{self.base_url.removesuffix('/v1')}/v1/systemone",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={
                        "model": self.model,
                        "state": state,
                        "questions": {"workflow": question},
                        "metadata": {"choregos_agent": ROUTER_AGENT},
                    },
                )
        except httpx.HTTPError as exc:
            raise RoutingError(f"the gateway did not answer: {type(exc).__name__}") from exc
        if response.status_code >= 400:
            raise RoutingError(f"the gateway answered {response.status_code}: {response.text[:200]}")
        try:
            body = response.json()
            answer = body["answers"]["workflow"]
            choice = str(answer["choice"])
            # The probability of the answer is `probabilities[choice]`, as a decision step reads it
            # (`choregos_adapters.decisions`). The answer's `confidence` is not: LiteLLM's gpt-6-luna
            # reports the margin over the runner-up there (0.93 against 0.07 gives 0.86, recorded in
            # prod on 2026-11-04), Laya a normalised entropy (#1303).
            confidence = float(answer["probabilities"][choice])
            if not 0.0 <= confidence <= 1.0:
                raise ValueError(f"a probability outside [0, 1]: {confidence}")
        except (ValueError, KeyError, TypeError) as exc:
            raise RoutingError("the decision model did not answer the question") from exc
        used = body.get("usage") or {}
        sent = len(json.dumps(state) + json.dumps(question))
        return Answer(
            workflow=choice or None,
            reason=f"the decision model chose `{choice}` with a probability of {confidence:.2f}",
            input_tokens=int(used.get("input_tokens") or sent // CHARS_PER_TOKEN),
            output_tokens=int(used.get("output_tokens") or 0),
            confidence=confidence,
        )


def get_decision_router(model: str, settings: Settings | None = None) -> WorkflowRouter:
    """The decision model's client, read from the same gateway as the chat model's."""
    settings = settings or get_settings()
    if settings.fakes:
        return FakeDecisionRouter()
    if not settings.gateway_master_key:
        raise RoutingError("no gateway key: the router has no model to call")
    return DecisionWorkflowRouter(settings.gateway_url, settings.gateway_master_key, model)


def get_workflow_router(model: str, settings: Settings | None = None) -> WorkflowRouter:
    """The single place that reads the gateway's address and key for routing (as the distiller's)."""
    settings = settings or get_settings()
    if settings.fakes:
        return FakeWorkflowRouter()
    if not settings.gateway_master_key:
        raise RoutingError("no gateway key: the router has no model to call")
    return GatewayWorkflowRouter(settings.gateway_url, settings.gateway_master_key, model)


@dataclass(slots=True)
class Decision:
    """What the model was asked and answered, once per birth."""

    workflow: str | None
    reason: str
    model: str
    called: bool = False
    #: The decision model's probability for its choice; `None` from a chat model.
    confidence: float | None = None
    #: The active workflow the model answered, even below the threshold; `None` otherwise. With
    #: `confidence`, what the decision ledger records of a routing (ADR 0123).
    choice: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    booked: bool = False


async def candidates_of(session: AsyncSession, project: Project) -> list[Candidate]:
    rows = (
        await session.execute(
            select(WorkflowDef)
            .where(WorkflowDef.project_id == project.id, WorkflowDef.is_active.is_(True))
            .order_by(WorkflowDef.name)
        )
    ).scalars()
    hints = dict(project.routing_hints or {})
    return [
        Candidate(
            name=row.name, description=workflow_model(row).metadata.description, hint=hints.get(row.name)
        )
        for row in rows
    ]


@dataclass(frozen=True, slots=True)
class RoutingModel:
    """Who is asked, on which route, and the catalogue's refusal if any."""

    name: str
    decision: bool
    refusal: str | None = None


async def _model_for(session: AsyncSession, org_id: str, settings: Settings) -> RoutingModel:
    """The first entry (by name) the catalogue enables for both `route` and `decide`, asked as a
    decision model; else the organisation's `cheap` tier, else the deployment's, asked as a chat model."""
    policy = await load_policy(session, org_id)
    deciders = sorted(
        name
        for name, entry in policy.entries.items()
        if entry.enabled and ROUTE_USAGE in entry.usages and DECIDE_USAGE in entry.usages
    )
    if deciders:
        return RoutingModel(deciders[0], decision=True)
    if settings.fakes:
        return RoutingModel(FakeWorkflowRouter.model, decision=False)
    model = policy.tier_model("cheap") or settings.default_model_profiles.get("cheap") or "platform/cheap"
    refusal = policy.check(model, None, usage=ROUTE_USAGE)
    return RoutingModel(model, False, f"{refusal} — change it in /admin/models" if refusal else None)


def _digest(project_id: str, model: str, candidates: list[Candidate], item: Item) -> str:
    return hashlib.sha256(f"{project_id}\n{model}\n{prompt_of(candidates, item)}".encode()).hexdigest()


async def ask_the_model(
    session: AsyncSession,
    project: Project,
    item: Item,
    *,
    settings: Settings | None = None,
) -> Decision:
    """The model's choice among the project's active workflows; `workflow` is `None` when it chose
    none or could not answer, and `reason` says which."""
    settings = settings or get_settings()
    candidates = await candidates_of(session, project)
    chosen = await _model_for(session, project.org_id, settings)
    memo: dict[str, Decision] = session.info.setdefault(_SESSION_KEY, {})
    key = _digest(project.id, chosen.name, candidates, item)
    if key in memo:
        return memo[key]
    if chosen.refusal is not None:
        decision = Decision(None, chosen.refusal, chosen.name)
    elif not candidates:
        decision = Decision(None, "the project has no active workflow", chosen.name)
    else:
        decision = await _ask(session, project, candidates, item, chosen, settings)
    memo[key] = decision
    return decision


async def _ask(
    session: AsyncSession,
    project: Project,
    candidates: list[Candidate],
    item: Item,
    chosen: RoutingModel,
    settings: Settings,
) -> Decision:
    try:
        if chosen.decision:
            router = get_decision_router(chosen.name, settings)
        else:
            router = get_workflow_router(chosen.name, settings)
        answer = await router.choose(candidates, item)
    except RoutingError as exc:
        logger.warning("workflow_routing.model_failed", project=project.slug, reason=str(exc))
        return Decision(None, str(exc), chosen.name)
    decision = Decision(
        workflow=None,
        reason=answer.reason,
        model=router.model,
        called=True,
        tokens_in=answer.input_tokens,
        tokens_out=answer.output_tokens,
        cost_usd=await _cost(session, project.org_id, router.model, answer),
        confidence=answer.confidence,
    )
    names = {c.name for c in candidates}
    threshold = project.routing_min_confidence
    if answer.workflow is None:
        decision.reason = answer.reason or "no workflow clearly fits"
        return decision
    if answer.workflow not in names:
        decision.reason = f"the model answered `{answer.workflow}`, which is not an active workflow"
        return decision
    decision.choice = answer.workflow
    if threshold is not None and answer.confidence is not None and answer.confidence < threshold:
        decision.reason = (
            f"the decision model chose `{answer.workflow}` with a probability of {answer.confidence:.2f}, "
            f"below the project's minimum of {threshold:.2f}"
        )
    else:
        decision.workflow = answer.workflow
    return decision
    decision.choice = answer.workflow
    if answer.workflow is None:
        pass
    elif threshold is not None and answer.confidence is not None and answer.confidence < threshold:
        decision.reason = (
            f"the decision model chose `{answer.workflow}` with a probability of {answer.confidence:.2f}, "
            f"below the project's minimum of {threshold:.2f}"
        )
    else:
        decision.workflow = answer.workflow
    return decision


async def _cost(session: AsyncSession, org_id: str, model: str, answer: Answer) -> float:
    price = await price_of(session, org_id, model)
    if price.priced_by == "none":
        return 0.0
    return answer.input_tokens / 1000 * (price.input_per_1k or 0.0) + answer.output_tokens / 1000 * (
        price.output_per_1k or 0.0
    )


async def book(session: AsyncSession, project: Project, decision: Decision, work_item_id: str | None) -> None:
    """One `route` line in the cost ledger for a call to the model, once, however often the decision
    is read."""
    if not decision.called or decision.booked:
        return
    from .costs import record_cost

    entry = await record_cost(
        session,
        project_id=project.id,
        work_item_id=work_item_id,
        run_id=None,
        provider=decision.model.split("/", maxsplit=1)[0] if "/" in decision.model else "",
        model=decision.model,
        backend=ROUTE_USAGE,
        stage_role=None,
        size=None,
        tokens_in=decision.tokens_in,
        tokens_out=decision.tokens_out,
        tokens_cached=0,
        cost_usd=decision.cost_usd,
        fx_rate=get_settings().fx_usd_eur,
        kind=ROUTE_USAGE,
    )
    entry.agent_slug = ROUTER_AGENT
    decision.booked = True


def routing_record(by: str, reason: str | None, decision: Decision | None) -> dict[str, Any]:
    """What the work item keeps of how it was routed (`documents.routing`)."""
    return {
        "by": by,
        "reason": reason,
        "model": decision.model if decision else None,
        "confidence": decision.confidence if decision else None,
    }


__all__ = [
    "DECIDE_USAGE",
    "DECISION_QUESTION",
    "INSTRUCTIONS",
    "ROUTER_AGENT",
    "ROUTE_USAGE",
    "STRATEGIES",
    "Answer",
    "Candidate",
    "Decision",
    "DecisionWorkflowRouter",
    "FakeDecisionRouter",
    "FakeWorkflowRouter",
    "GatewayWorkflowRouter",
    "Item",
    "RoutingError",
    "Strategy",
    "WorkflowRouter",
    "ask_the_model",
    "book",
    "candidates_of",
    "criteria_of",
    "get_decision_router",
    "get_workflow_router",
    "routing_record",
]
