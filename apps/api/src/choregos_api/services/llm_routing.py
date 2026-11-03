# SPDX-License-Identifier: Apache-2.0
"""A small model chooses the workflow of a work item that names none (ADR 0116).

The project's routing strategy decides when it is asked: never (`labels`), when no rule matched
(`rules_then_llm`), or instead of the rules (`llm`). It reads the item — title, body, labels, type —
and, for each active workflow, its name, its description and the hint written in the console. It
answers one workflow among them or none; anything else (an unknown name, a timeout, an error, a
model the catalogue refuses, no gateway key) leaves the work item to the default. The birth never
fails because of the model.

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


async def _model_for(session: AsyncSession, org_id: str, settings: Settings) -> tuple[str, str | None]:
    """The organisation's `cheap` tier, else the deployment's; and the catalogue's refusal, if any."""
    if settings.fakes:
        return FakeWorkflowRouter.model, None
    policy = await load_policy(session, org_id)
    model = policy.tier_model("cheap") or settings.default_model_profiles.get("cheap") or "platform/cheap"
    refusal = policy.check(model, None, usage=ROUTE_USAGE)
    return model, (f"{refusal} — change it in /admin/models" if refusal else None)


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
    model, refusal = await _model_for(session, project.org_id, settings)
    memo: dict[str, Decision] = session.info.setdefault(_SESSION_KEY, {})
    key = _digest(project.id, model, candidates, item)
    if key in memo:
        return memo[key]
    if refusal is not None:
        decision = Decision(None, refusal, model)
    elif not candidates:
        decision = Decision(None, "the project has no active workflow", model)
    else:
        decision = await _ask(session, project, candidates, item, model, settings)
    memo[key] = decision
    return decision


async def _ask(
    session: AsyncSession,
    project: Project,
    candidates: list[Candidate],
    item: Item,
    model: str,
    settings: Settings,
) -> Decision:
    try:
        router = get_workflow_router(model, settings)
        answer = await router.choose(candidates, item)
    except RoutingError as exc:
        logger.warning("workflow_routing.model_failed", project=project.slug, reason=str(exc))
        return Decision(None, str(exc), model)
    decision = Decision(
        workflow=None,
        reason=answer.reason,
        model=router.model,
        called=True,
        tokens_in=answer.input_tokens,
        tokens_out=answer.output_tokens,
        cost_usd=await _cost(session, project.org_id, router.model, answer),
    )
    names = {c.name for c in candidates}
    if answer.workflow is None:
        decision.reason = answer.reason or "no workflow clearly fits"
    elif answer.workflow not in names:
        decision.reason = f"the model answered `{answer.workflow}`, which is not an active workflow"
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
    from .couts import record_cost

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
    return {"by": by, "reason": reason, "model": decision.model if decision else None}


__all__ = [
    "INSTRUCTIONS",
    "ROUTER_AGENT",
    "ROUTE_USAGE",
    "STRATEGIES",
    "Answer",
    "Candidate",
    "Decision",
    "FakeWorkflowRouter",
    "GatewayWorkflowRouter",
    "Item",
    "RoutingError",
    "Strategy",
    "WorkflowRouter",
    "ask_the_model",
    "book",
    "candidates_of",
    "get_workflow_router",
    "routing_record",
]
