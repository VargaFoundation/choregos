# SPDX-License-Identifier: Apache-2.0
"""A person talking to a prompt agent directly (S41-09, ADR 0080 §10): a session, next to runs.

A session opens on its first response. It resolves the version the project is served (its pin, its
channel, its overrides), resolves the model, reserves from the same budgets as a run (the caller's
cap, the agent's, the project's day, the organisation's month) and mints ONE gateway key at that
amount. The key is sealed in the secret store, never returned; its `gateway_keys` row is named
`session:<id>`, so `open_reservations` counts it while it lives, like a run's key.

Each response runs the prompt loop of a workflow step (`choregos_core.prompt_loop`) on the session's
history: the agent's instructions, its model, the read tools its `mcp_servers` grant in the project.
A write or an approval tool is not offered in a session: it is a governed action that needs a work
item. Every tool call goes through the courtier, the injection guard and the ledger, as a run's.
Neither a model call nor a tool call holds a database connection while it is answered (#1376).

Closing (by its person, at its expiry, or when the gateway refuses the key for budget) reads the
key's spend, books it in the ledger with `session_id`, revokes the key and forgets its secret. It is
idempotent: a session closed twice is closed once.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import httpx
from choregos_adapters.registry import fakes_enabled
from choregos_contracts import EventType, ProjectConfig
from choregos_core import GatewayModel, ModelResolutionError, ModelResolver, Spend, utcnow
from choregos_core.domain import aware
from choregos_core.prompt_loop import LoopOutcome, LoopRequest, LoopTool, run_loop
from choregos_core.tool_answer import render_text
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db.models import (
    AgentSessionEvent,
    AgentSessionRecord,
    CostLedger,
    GatewayKeyRow,
    ModelCatalogueRow,
    Organization,
    Project,
    User,
)
from ..db.session import (
    allow_release_during_calls,
    outside_the_transaction,
    release_allowed,
    release_connection,
)
from ..errors import ApiError, conflict, unprocessable
from ..logging import get_logger
from ..schemas.agent_sessions import (
    AgentOutputMessage,
    AgentOutputText,
    AgentResponse,
    AgentResponseError,
    AgentResponseUsage,
    AgentSession,
    AgentSessionEventDto,
)
from ..secret_store import get_secret_store
from .events import persist_event
from .identity_exchange import SERVICE, USER_EXCHANGE, Acting

if TYPE_CHECKING:
    from choregos_adapters.base import GatewayAdapter
    from choregos_core.policy import PolicyEngine

    from ..rbac import Principal
    from ..schemas import AgentSpec
    from .broker import BrokerTool

log = get_logger(__name__)

#: How long a session lives: its key's TTL, and when the next open in its project sweeps it.
SESSION_TTL_S = 3600
#: What a session may spend when its caller names no cap (USD); the project's budgets still bind.
DEFAULT_SESSION_USD = 1.0
#: A session's turns per response when its agent names none.
DEFAULT_MAX_TURNS = 20
#: The `gateway_keys.run_id` of a session's key.
KEY_PREFIX = "session:"

SessionRuntime = Literal["prompt", "voice", "socket"]


@dataclass(frozen=True, slots=True)
class ConversationTerms:
    """What makes a session a conversation (ADR 0134): it holds no key of its own, each turn mints one."""

    max_usd: float
    idle_close_minutes: int
    context: dict[str, Any] | None = None
    installation_id: str | None = None


_DOORS: dict[str, str] = {"prompt": "/responses", "voice": "/realtime", "socket": "/realtime"}
#: The runtimes each door serves: a hosted agent answers on `/responses` too, from its pod (S41-11),
#: and on `/realtime` when its container serves `invocations_ws` (S41-21).
_SERVED: dict[str, set[str]] = {"prompt": {"prompt", "hosted"}, "voice": {"voice"}, "socket": {"hosted"}}
#: The reasons a session ends that are not failures.
_CLEAN_ENDS = {"closed", "expired", "max_minutes", "idle", "caller_gone", "agent_closed"}

#: The gateway's own words for a spent key, as `choregos_runner.budget` reads them.
_BUDGET_SIGNATURES = ("budget_exceeded", "budgetexceedederror", "exceededbudget", "budget has been exceeded")

#: Tests and `CHOREGOS_FAKES` put their gateway and model transport here.
_OVERRIDES: dict[str, Any] = {}


def set_session_gateway(gateway: GatewayAdapter | None) -> None:
    """For tests and the fakes: the gateway sessions mint, read and revoke at (`None` forgets it)."""
    _OVERRIDES.pop("gateway", None)
    if gateway is not None:
        _OVERRIDES["gateway"] = gateway


def set_model_transport(transport: httpx.AsyncBaseTransport | None) -> None:
    """For tests: the transport the model calls of a session go through (`None` forgets it)."""
    _OVERRIDES.pop("transport", None)
    if transport is not None:
        _OVERRIDES["transport"] = transport


async def session_gateway(session: AsyncSession, org_id: str) -> GatewayAdapter:
    if "gateway" in _OVERRIDES:
        found: GatewayAdapter = _OVERRIDES["gateway"]
        return found
    if fakes_enabled():
        # One fake for the process: a key minted at open must be the one read and revoked at close.
        from choregos_adapters.fakes import FakeGateway

        return _OVERRIDES.setdefault("gateway", FakeGateway())  # type: ignore[no-any-return]
    from .gateway import org_gateway

    return await org_gateway(session, org_id)


def secret_name(session_id: str) -> str:
    return f"agent-session/{session_id}/key"


class _ModelRefused(Exception):  # noqa: N818 - a refusal of the model, said in the response
    def __init__(self, status: int, text: str) -> None:
        super().__init__(f"the model answered {status}: {text[:300]}")


def budget_refusal(text: str) -> bool:
    lowered = text.lower()
    return any(signature in lowered for signature in _BUDGET_SIGNATURES)


# ───────────────────────────── opening ─────────────────────────────


async def open_session(
    session: AsyncSession,
    principal: Principal,
    organisation: Organization,
    agent_slug: str,
    project: Project,
    *,
    max_usd: float | None,
    runtime: SessionRuntime = "prompt",
    conversation: ConversationTerms | None = None,
) -> AgentSessionRecord:
    """A new session of the agent in the project: its version, its model, its reservation, its key.

    `runtime` is the door it opens at: `prompt` on `/responses`, `voice` on `/realtime` (S41-10),
    `socket` on `/realtime` for a hosted agent's own socket (S41-21). With `conversation`, the session is a
    durable conversation (S63-04): no reservation and no key here, each turn reserves and mints its own."""
    from .agents import AgentUnavailable, resolve_agent

    await sweep_expired(session, project.id)
    try:
        resolved = await resolve_agent(session, organisation.id, project.id, agent_slug)
    except AgentUnavailable as refusal:
        raise conflict(str(refusal)) from refusal
    spec = resolved.spec
    kind = spec.runtime.kind if spec.runtime is not None else None
    if conversation is not None and kind != "prompt":
        raise unprocessable(
            f"`{agent_slug}` is not a prompt agent (runtime `{kind or 'none'}`): a conversation needs one",
            [{"loc": ["agent", "runtime"], "msg": f"runtime `{kind or 'none'}` has no conversation"}],
        )
    if kind not in _SERVED[runtime]:
        raise unprocessable(
            f"`{agent_slug}` is not a {runtime} agent (runtime `{kind or 'none'}`): "
            f"only a {' or '.join(sorted(_SERVED[runtime]))} agent answers on `{_DOORS[runtime]}`",
            [{"loc": ["agent", "runtime"], "msg": f"runtime `{kind or 'none'}` has no session here"}],
        )
    if kind == "hosted":
        from .agent_hosted_sessions import hosted_protocol

        protocols = list(getattr(spec.runtime, "protocols", []) or [])
        if runtime == "socket" and "invocations_ws" not in protocols:
            raise unprocessable(
                f"`{agent_slug}` does not serve `invocations_ws`: a session reaches it on `/responses`",
                [{"loc": ["agent", "runtime", "protocols"], "msg": "no socket to relay"}],
            )
        if runtime == "prompt" and hosted_protocol(spec) is None:
            raise unprocessable(
                f"`{agent_slug}` serves only `invocations_ws`: a session reaches it on `/realtime`",
                [{"loc": ["agent", "runtime", "protocols"], "msg": "no protocol `/responses` speaks"}],
            )
    # A voice session lives as long as its agent lets it speak, its key with it.
    ttl_s = max(SESSION_TTL_S, getattr(spec.runtime, "max_session_minutes", 0) * 60 + 60)
    config = ProjectConfig.model_validate(project.config or {})
    gateway = await session_gateway(session, organisation.id)
    model = await _resolve_model(session, organisation.id, gateway, spec, config)
    # A voice session is measured by its relay (S41-15): its prices are settled before any key.
    terms = (
        await _voice_terms(session, organisation.id, gateway, model.litellm_model)
        if runtime == "voice"
        else {}
    )

    record = AgentSessionRecord(
        id=str(uuid4()),
        org_id=organisation.id,
        project_id=project.id,
        agent_slug=resolved.agent.slug,
        agent_version=resolved.version,
        user_id=principal.user_id,
        status="open",
        model=model.litellm_model,
        messages=[{"role": "system", "content": spec.instructions}] if spec.instructions else [],
        responses=0,
        tool_calls=0,
        cost_usd=0.0,
        expires_at=utcnow() + timedelta(seconds=ttl_s),
    )
    if conversation is not None:
        now = utcnow()
        record.kind = "conversation"
        record.installation_id = conversation.installation_id
        record.context = conversation.context
        record.resolved_subjects = []
        record.agent_revision = resolved.version
        record.turns = 0
        record.max_usd = conversation.max_usd
        record.idle_close_minutes = conversation.idle_close_minutes
        record.last_activity_at = now
        record.expires_at = now + timedelta(minutes=conversation.idle_close_minutes)
        session.add(record)
        await session.flush()
        await journal(
            session,
            record,
            "conversation.opened",
            {"model": record.model, "agent_revision": resolved.version, "max_usd": conversation.max_usd},
        )
        return record
    session.add(record)
    await session.flush()
    reservation = await reserve_budget(session, organisation.id, project, resolved.agent.slug, spec, max_usd)
    from .budgets import DEFAULT_FLOOR_USD

    if reservation.below(DEFAULT_FLOOR_USD):
        raise ApiError(409, "Budget exhausted", f"the session does not open: {reservation.describe()}")

    key = await mint_key(
        session,
        gateway,
        record,
        project,
        budget_usd=reservation.usd,
        ttl_s=ttl_s,
        run_id=f"{KEY_PREFIX}{record.id}",
    )
    record.gateway_key_id = key.key_id
    await get_secret_store().put(
        session, organisation.id, secret_name(record.id), str(key.key), principal.email
    )
    await journal(
        session,
        record,
        "session.opened",
        {"model": record.model, "reserved_usd": reservation.usd, "bound_by": reservation.bound_by, **terms},
    )
    log.info(
        "agent_session.opened",
        project=project.slug,
        agent=record.agent_slug,
        session_id=record.id,
        reserved_usd=reservation.usd,
    )
    return record


async def mint_key(
    session: AsyncSession,
    gateway: GatewayAdapter,
    record: AgentSessionRecord,
    project: Project,
    *,
    budget_usd: float,
    ttl_s: int,
    run_id: str,
) -> Any:
    """A gateway key for the session's model at `budget_usd`, with its `gateway_keys` row named `run_id`
    (`session:<id>`, or `turn:<id>:<n>` for a conversation's turn): the project's team, the guard's mark."""
    from choregos_core.policy import PolicyEngine

    from .definitions import effective_policy

    team_id = None
    policy = await effective_policy(session, project.id)
    if (daily := PolicyEngine(policy).daily_budget()) is not None:
        team_id = await gateway.ensure_team(f"choregos-{project.id}", daily)
    key_metadata: dict[str, Any] = {
        "project": project.slug,
        "agent": record.agent_slug,
        "agent_version": record.agent_version,
        "session_id": record.id,
        "user": record.user_id,
    }
    if policy.sandbox.llm_security_analyzer:
        # The gateway's guard masks secrets for this key, as for a run's (ADR 0084).
        from choregos_adapters.base import llm_guard_refusal
        from choregos_core.llm_guardrail import GUARD_METADATA_KEY, GUARD_REDACT

        if (unguarded := llm_guard_refusal(gateway)) is not None:
            raise unprocessable(
                unguarded, [{"loc": ["policy", "sandbox", "llm_security_analyzer"], "msg": unguarded}]
            )
        key_metadata[GUARD_METADATA_KEY] = GUARD_REDACT
    key = await gateway.mint_key(
        key_metadata, budget_usd=budget_usd, ttl_s=ttl_s, models=[record.model], team_id=team_id
    )
    session.add(
        GatewayKeyRow(
            key_id=key.key_id,
            run_id=run_id,
            project_id=project.id,
            budget_usd=budget_usd,
            expires_at=key.expires_at,
        )
    )
    return key


async def _resolve_model(
    session: AsyncSession, org_id: str, gateway: GatewayAdapter, spec: AgentSpec, config: ProjectConfig
) -> Any:
    from .gateway import org_gateway_url
    from .model_catalogue import load_policy

    resolver = ModelResolver(
        # The session's key comes from the organisation's gateway: its models are called there (ADR 0109).
        gateway_url=await org_gateway_url(session, org_id) or get_settings().gateway_url,
        gateway_models=await gateway.list_models(),
        catalogue=await load_policy(session, org_id),
    )
    try:
        return resolver.resolve(spec.model or "profile:by_size", project=config, backend="prompt")
    except ModelResolutionError as refusal:
        raise unprocessable(str(refusal), [{"loc": ["agent", "model"], "msg": str(refusal)}]) from refusal


async def _voice_terms(
    session: AsyncSession, org_id: str, gateway: GatewayAdapter, name: str
) -> dict[str, Any]:
    """The prices and the per-minute rate a voice session of the model `name` is measured and capped
    at, as `session.opened` keeps them; a model with no audio price opens none."""
    from .realtime_spend import realtime_prices

    listed = [m for m in await gateway.list_models() if name in (m.model_name, m.litellm_model)]
    model = listed[0] if listed else GatewayModel(model_name=name, litellm_model=name)
    row = (
        (
            await session.execute(
                select(ModelCatalogueRow).where(
                    ModelCatalogueRow.org_id == org_id,
                    ModelCatalogueRow.model_name.in_({model.model_name, model.litellm_model}),
                )
            )
        )
        .scalars()
        .first()
    )
    prices = realtime_prices(model, row)
    if prices is None:
        message = (
            f"the gateway gives `{model.model_name}` no audio price, and the catalogue no price override: "
            "a voice session it could not measure does not open"
        )
        raise unprocessable(message, [{"loc": ["agent", "model"], "msg": "no realtime price"}])
    return {"prices": prices.as_payload(), "max_usd_per_minute": row.max_usd_per_minute if row else None}


async def reserve_budget(
    session: AsyncSession,
    org_id: str,
    project: Project,
    agent_slug: str,
    spec: AgentSpec,
    max_usd: float | None,
    extra_caps: dict[str, float] | None = None,
) -> Any:
    """The smallest of what is left under every cap that applies, as `prepare_stage` reads it.

    `extra_caps` are further remainders the caller computed (an assistant's person and installation
    months, S63-15), named as `bound_by` should report them."""
    from choregos_core.policy import PolicyEngine

    from .agents import spend_this_month, spend_today
    from .budgets import (
        lock_budget_scopes,
        open_reservations,
        org_spent_since,
        project_spent_since,
        reserve,
        start_of_day,
        start_of_month,
    )
    from .definitions import ceiling_of, effective_policy

    engine = PolicyEngine(await effective_policy(session, project.id))
    ceiling = ceiling_of(await session.get(Organization, org_id))
    org_cap = ceiling.budgets.monthly_org_usd if ceiling is not None else None
    budget = spec.budget
    scopes = []
    if engine.daily_budget() is not None:
        scopes.append(f"project:{project.id}")
    if org_cap is not None:
        scopes.append(f"org:{org_id}")
    if budget.daily_usd is not None or budget.monthly_usd is not None:
        scopes.append(f"agent:{org_id}:{agent_slug}")
    await lock_budget_scopes(session, scopes)

    left: dict[str, float | None] = {
        "session": max_usd if max_usd is not None else DEFAULT_SESSION_USD,
        "agent_run": budget.run_usd,
    }
    if budget.daily_usd is not None:
        left["agent_day"] = budget.daily_usd - await spend_today(session, org_id, agent_slug)
    if budget.monthly_usd is not None:
        left["agent_month"] = budget.monthly_usd - await spend_this_month(session, org_id, agent_slug)
    if (day := engine.daily_budget()) is not None:
        left["project_day"] = (
            day
            - await project_spent_since(session, project.id, start_of_day())
            - await open_reservations(session, project_id=project.id)
        )
    if org_cap is not None:
        left["org_month"] = (
            org_cap
            - await org_spent_since(session, org_id, start_of_month())
            - await open_reservations(session, org_id=org_id)
        )
    left.update(extra_caps or {})
    return reserve(left)


# ───────────────────────────── responding ─────────────────────────────


async def respond(
    session: AsyncSession,
    record: AgentSessionRecord,
    project: Project,
    inputs: list[str],
    *,
    turn_key: str | None = None,
) -> AgentResponse:
    """One response of the session: the person's input, the agent's loop, what it said.

    No connection of the request is held while the model or a tool answers (#1376): the request's
    transaction commits before each call leaves (`outside_the_transaction`), and what the call
    brings back is written in the next one, in the same scope. So the journal commits turn by turn —
    a call that was made, and paid on the key, stays written when the response then fails —, while
    the history (`record.messages`) and the response's own line commit together at its end: a
    response that failed half-way leaves no half of a turn in what the next one sends the model."""
    from .agents import resolve_agent

    if record.status != "open" or aware(record.expires_at) <= utcnow():  # type: ignore[operator]
        if record.status == "open":
            from ..db.session import release_connection

            await close_session(session, record, reason="expired")
            # The close revoked the key at the gateway: its rows are committed before the refusal
            # below, which rolls the request's transaction back (#1409).
            await release_connection(session)
        raise conflict(f"session {record.id} is {record.status} ({record.reason}): open a new one")
    resolved = await resolve_agent(
        session, record.org_id, project.id, f"{record.agent_slug}@{record.agent_version}"
    )
    spec: AgentSpec = resolved.spec
    runtime = spec.runtime
    if runtime is not None and runtime.kind == "hosted":
        from .agent_hosted_sessions import respond_hosted

        return await respond_hosted(session, record, project, spec, inputs)
    # What the request wrote so far (the session it opened, its reservation, the input) need not wait
    # for the model's answer to be kept: the first call commits it, and releases the budget's lock.
    allow_release_during_calls(session)
    if turn_key is not None:
        # A conversation's turn: the key was minted for this turn alone, and is not in the secret store.
        key = turn_key
    else:
        keys = await get_secret_store().get_many(session, record.org_id, [secret_name(record.id)])
        key = keys.get(secret_name(record.id), "")
    tools = await session_tools(session, spec, project)
    messages = list(record.messages or [])
    for text in inputs:
        messages.append({"role": "user", "content": text})
        await journal(session, record, "input.text", {"text": text[:2000]})
    response_id = f"resp_{uuid4().hex}"
    from .gateway import org_gateway_url

    # The gateway the session's key was minted on (ADR 0109): the organisation's own, else the deployment's.
    gateway_url = await org_gateway_url(session, record.org_id) or get_settings().gateway_url
    #: What the response asked for, said when its booking fails after the calls were made.
    made = {"model_calls": 0, "tool_calls": 0}

    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        # Up to 300 s: the request's transaction does not wait for it on a pooled connection.
        async with (
            outside_the_transaction(session),
            httpx.AsyncClient(transport=_OVERRIDES.get("transport"), timeout=300) as http,
        ):
            made["model_calls"] += 1
            answer = await http.post(
                f"{gateway_url.rstrip('/')}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key}"},
            )
        if answer.status_code != 200:
            raise _ModelRefused(answer.status_code, answer.text)
        parsed = answer.json()
        if not isinstance(parsed, dict):
            raise _ModelRefused(answer.status_code, "the model answered something that is not a completion")
        return parsed

    identities = {server.connector: server.identity for server in spec.mcp_servers if server.identity}

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        made["tool_calls"] += 1
        return await call_session_tool(session, record, project, tools[name], arguments, identities)

    async def on_event(type_: str, payload: dict[str, Any]) -> None:
        await journal(session, record, type_, payload)

    outcome: LoopOutcome | None = None
    error: AgentResponseError | None = None
    try:
        try:
            outcome = await run_loop(
                LoopRequest(
                    model=record.model,
                    max_turns=spec.limits.max_turns or DEFAULT_MAX_TURNS,
                    max_output_tokens=getattr(runtime, "max_output_tokens", None),
                    tool_choice=getattr(runtime, "tool_choice", "auto"),
                    response_format=getattr(runtime, "response_format", "text"),
                ),
                messages,
                [
                    LoopTool(t.name, str(t.announcement().get("description") or ""), tool_parameters(t))
                    for t in tools.values()
                ],
                complete,
                call_tool,
                on_event=on_event,
            )
        except (_ModelRefused, httpx.HTTPError, ValueError) as exc:
            code = "budget_exceeded" if budget_refusal(str(exc)) else "model_error"
            error = AgentResponseError(code=code, message=str(exc)[:500])
        return await _book_the_response(session, record, response_id, messages, outcome, error)
    except Exception as failure:
        # Calls were made (and the journal may hold their lines) but the response is not booked:
        # said here, with what it had asked for, besides the 500 the caller gets.
        log.warning(
            "agent_session.response_not_booked",
            session_id=record.id,
            project=project.slug,
            response_id=response_id,
            error=type(failure).__name__,
            **made,
        )
        raise


async def _book_the_response(
    session: AsyncSession,
    record: AgentSessionRecord,
    response_id: str,
    messages: list[dict[str, Any]],
    outcome: LoopOutcome | None,
    error: AgentResponseError | None,
) -> AgentResponse:
    """What a response keeps once its last call answered: the history, the count, its line and the
    close of a spent key, in one transaction committed here, in the scope the calls left with."""
    if outcome is not None and outcome.status == "max_turns":
        error = AgentResponseError(
            code="max_turns", message=f"stopped after {outcome.turns} model calls without an answer"
        )
    record.messages = messages
    record.responses += 1
    text = outcome.text if outcome is not None and error is None else ""
    usage = AgentResponseUsage(
        input_tokens=(outcome.usage.get("prompt_tokens", 0) if outcome else 0),
        output_tokens=(outcome.usage.get("completion_tokens", 0) if outcome else 0),
        turns=outcome.turns if outcome else 0,
        tool_calls=outcome.tool_calls if outcome else 0,
    )
    status = "failed" if error is not None else "completed"
    await journal(
        session,
        record,
        f"response.{status}",
        {"response_id": response_id, "text": text[:2000], **({"error": error.model_dump()} if error else {})},
    )
    if error is not None and error.code == "budget_exceeded" and record.kind != "conversation":
        # The key is spent: nothing more can be said in this session. (A conversation's turn key ends with
        # its turn; the next turn is reserved from what is left of the conversation's cap.)
        await close_session(session, record, reason="budget_exceeded")
    await release_connection(session)
    return AgentResponse(
        id=response_id,
        session_id=record.id,
        status=status,
        output=[AgentOutputMessage(content=[AgentOutputText(text=text)])] if text else [],
        output_text=text,
        usage=usage,
        error=error,
    )


def tool_parameters(tool: BrokerTool) -> dict[str, Any]:
    schema = tool.announcement().get("inputSchema")
    return dict(schema) if isinstance(schema, dict) else {"type": "object", "properties": {}}


async def session_tools(session: AsyncSession, spec: AgentSpec, project: Project) -> dict[str, BrokerTool]:
    """The read tools the agent's `mcp_servers` grant in the project: a write or an approval is a
    governed action, which needs a work item, so a session is not offered one."""
    from .broker import selection_of_spec, tools_of_selection

    granted = await tools_of_selection(session, selection_of_spec(spec.model_dump(mode="json")), project)
    return {name: tool for name, tool in granted.items() if not tool.is_write and not tool.needs_approval}


@dataclass(frozen=True, slots=True)
class SessionToolAnswer:
    """A session's tool call, answered (`AgentSessionToolAnswer`): the text the model reads, and what
    changes its meaning, as a run's call says it (`callRunTool`, #1349) — the status the call ended
    with (the server's, `400` for arguments the schema refuses, `429` past the cap, `451` for a result
    withheld), the calls the cap leaves the session (`None` without a cap), the injection guard's
    flags (`sandbox.prompt_injection` `warn` or `block`)."""

    text: str
    status_code: int = 200
    remaining: int | None = None
    suspicions: list[dict[str, Any]] = field(default_factory=list)

    def for_the_model(self) -> str:
        """The text with its notices, for a channel with no error flag (#1399), composed as a hosted
        session's runner composes its MCP result (`choregos_core.tool_answer`)."""
        answer = {"status_code": self.status_code, "remaining": self.remaining, "suspicions": self.suspicions}
        return render_text(answer, self.text, "session")


def _calls_left(cap: int, record: AgentSessionRecord) -> int | None:
    return max(0, cap - record.tool_calls) if cap else None


async def call_session_tool(
    session: AsyncSession,
    record: AgentSessionRecord,
    project: Project,
    tool: BrokerTool,
    arguments: dict[str, Any],
    identities: Mapping[str, str] | None = None,
) -> str:
    """What a loop that gives its model text reads of `answer_session_tool` (a prompt session's tool
    message, a realtime session's `function_call_output`): the courtier's text between the same
    notices a hosted session's runner says (#1399) — the injection guard's flags first, the calls the
    cap leaves the session last — and, since neither channel has an error flag, a status of 400 or
    more said first (`choregos_core.tool_answer.render_text`)."""
    return (await answer_session_tool(session, record, project, tool, arguments, identities)).for_the_model()


async def answer_session_tool(
    session: AsyncSession,
    record: AgentSessionRecord,
    project: Project,
    tool: BrokerTool,
    arguments: dict[str, Any],
    identities: Mapping[str, str] | None = None,
) -> SessionToolAnswer:
    """One read through the courtier, as a run's: the policy's cap, the schema, the connector's key
    resolved here (or, in `identities`, the person's exchanged token, ADR 0135: the mode the agent
    version picks per connector, `service` for any it does not list), the injection guard, a ledger
    line. What the model reads back is text: the text
    blocks and, when they do not serialize it, the `structuredContent` (S40-18c), guarded as read."""
    from choregos_core.policy import PolicyEngine

    from .broker import ArgumentsRefused, call, check_arguments
    from .definitions import effective_policy

    engine = PolicyEngine(await effective_policy(session, project.id))
    cap = engine.max_tool_calls_per_run()
    if cap and record.tool_calls >= cap:
        text = f"tool call cap reached: this session already called {record.tool_calls} tools (cap {cap})"
        return SessionToolAnswer(text, 429, 0)
    try:
        check_arguments(tool, arguments)
    except ArgumentsRefused as refusal:
        return SessionToolAnswer(str(refusal), 400, _calls_left(cap, record))
    # The server is called with no connection of the request held when its owner allowed it
    # (`allow_release_during_calls`, #1376): the broker commits first. What follows is the call's
    # booking — the count, its line, its events with their notification —, one transaction after the
    # call, committed here in the scope the call left with; all of it is kept or none.
    acting = await _acting(session, record, tool, identities)
    code, body = await call(session, tool, arguments, project=project, acting=acting)
    provider = f"mcp:{tool.connector.name}"
    try:
        answer = await _book_the_tool_call(session, record, project, tool, engine, cap, code, body, acting)
        if release_allowed(session):
            await release_connection(session)
    except Exception as failure:
        # The call was made and is not booked: said here, with what its line would have held,
        # besides the error its caller gets.
        log.warning(
            "tool_call.not_booked",
            session_id=record.id,
            project=project.slug,
            tool=tool.name,
            provider=provider,
            status_code=code,
            cost_usd=tool.operation.price_usd or 0.0,
            error=type(failure).__name__,
        )
        raise
    return answer


async def _acting(
    session: AsyncSession, record: AgentSessionRecord, tool: BrokerTool, identities: Mapping[str, str] | None
) -> Acting:
    """Who this call is for: the person of the conversation when the agent version picks `user_exchange`
    for the connector, the connector's own identity otherwise (ADR 0135)."""
    if (identities or {}).get(tool.connector.name) != USER_EXCHANGE:
        return Acting(SERVICE)
    user = await session.get(User, record.user_id) if record.user_id else None
    return Acting(
        USER_EXCHANGE,
        user_id=record.user_id or "",
        subject=(user.oidc_sub if user is not None else None) or record.user_id or "",
        org_id=record.org_id,
        actor=f"assistant:{record.agent_slug}",
    )


async def _book_the_tool_call(  # noqa: PLR0913 - one call, fact by fact
    session: AsyncSession,
    record: AgentSessionRecord,
    project: Project,
    tool: BrokerTool,
    engine: PolicyEngine,
    cap: int,
    code: int,
    body: Any,
    acting: Acting | None = None,
) -> SessionToolAnswer:
    """What the model reads back from a session's tool call, guarded, and what changes its meaning;
    the call's count, line and events."""
    from choregos_core.injection import suspicions as find

    from .broker import session_text

    provider = f"mcp:{tool.connector.name}"
    refused_by_network = isinstance(body, dict) and body.get("type") in {"network_policy", "rate_limited"}
    text = session_text(body) if code < 400 else json.dumps(body, default=str)[:2000]
    mode = engine.policy.sandbox.prompt_injection
    found = [] if mode == "ignore" or refused_by_network else find(text, source=f"tool.{tool.name}")
    if found:
        await persist_event(
            session,
            EventType.SECURITY_INJECTION_SUSPECTED,
            project_id=project.id,
            project_slug=project.slug,
            subject=record.id,
            session_id=record.id,
            tool=tool.name,
            suspicions=[s.to_dict() for s in found],
        )
        if mode == "block":
            text = f"result of {tool.name} withheld: suspected injection — a human reviews it"
    answered = 451 if found and mode == "block" else code
    if not refused_by_network:
        record.tool_calls += 1
        session.add(
            CostLedger(
                project_id=project.id,
                session_id=record.id,
                ts=utcnow(),
                kind="tool",
                provider=provider,
                model=tool.name,
                cost_usd=tool.operation.price_usd or 0.0,
                agent_slug=record.agent_slug,
                agent_version=record.agent_version,
            )
        )
    await persist_event(
        session,
        EventType.TOOL_CALLED,
        project_id=project.id,
        project_slug=project.slug,
        subject=record.id,
        session_id=record.id,
        tool=tool.name,
        provider=provider,
        status_code=code,
        **(acting or Acting(SERVICE)).audit(tool.connector.name),
    )
    flagged = [s.to_dict() for s in found]
    return SessionToolAnswer(text, answered, _calls_left(cap, record), flagged)


# ───────────────────────────── closing ─────────────────────────────


async def close_session(session: AsyncSession, record: AgentSessionRecord, *, reason: str = "closed") -> None:
    """Books the key's spend with `session_id`, revokes the key, forgets its secret. Idempotent."""
    if record.status != "open":
        return
    from .costs import record_cost
    from .model_catalogue import priced_spend

    gateway = await session_gateway(session, record.org_id)
    settings = get_settings()
    key_row = None
    priced: dict[str, Any] = {}
    if record.gateway_key_id:
        key_row = (
            await session.execute(select(GatewayKeyRow).where(GatewayKeyRow.key_id == record.gateway_key_id))
        ).scalar_one_or_none()
        spend = await gateway.spend(record.gateway_key_id)
        relayed = await _relayed(session, record)
        if relayed is not None:
            # A voice session books what its relay measured (S41-15); the gateway's count is kept
            # beside it, never booked.
            priced = {"priced_by": "relay", "gateway_cost_usd": spend.cost_usd}
            spend = relayed
        else:
            spend, priced = await priced_spend(
                session,
                record.org_id,
                [record.model, *spend.models_used],
                spend,
                measured="spend_readback" in getattr(gateway, "capabilities", {"spend_readback"}),
            )
        entry = await record_cost(
            session,
            project_id=record.project_id,
            work_item_id=None,
            run_id=None,
            provider=spend.models_used[0].split("/")[0] if spend.models_used else "",
            model=record.model,
            backend="prompt",
            stage_role=None,
            size=None,
            tokens_in=spend.tokens_in,
            tokens_out=spend.tokens_out,
            tokens_cached=spend.tokens_cached,
            cost_usd=spend.cost_usd,
            fx_rate=settings.fx_usd_eur,
        )
        entry.session_id = record.id
        entry.agent_slug, entry.agent_version = record.agent_slug, record.agent_version
        record.cost_usd = round(record.cost_usd + spend.cost_usd, 6)
        try:
            await gateway.revoke(record.gateway_key_id)
            revoked = True
        except Exception as error:  # a cleanup: the key still dies at its TTL
            log.warning("agent_session.key_not_revoked", session_id=record.id, error=str(error)[:300])
            revoked = False
        if key_row is not None:
            key_row.revoked = revoked
            key_row.spend_usd = spend.cost_usd
    await get_secret_store().delete(session, record.org_id, secret_name(record.id))
    if record.pod is not None:
        from .agent_hosted_sessions import stop_pod

        await stop_pod(record, reason)
    record.status = "closed" if reason in _CLEAN_ENDS else "failed"
    record.reason = reason
    record.closed_at = utcnow()
    closed = {"reason": reason, "cost_usd": record.cost_usd}
    if priced.get("priced_by") == "relay":
        closed.update(priced)
    await journal(session, record, "session.closed", closed)
    log.info("agent_session.closed", session_id=record.id, reason=reason, cost_usd=record.cost_usd)


async def opened_terms(session: AsyncSession, record: AgentSessionRecord) -> dict[str, Any]:
    """What `session.opened` journaled: the reservation and, for a voice session, its prices."""
    payload = (
        (
            await session.execute(
                select(AgentSessionEvent.payload)
                .where(AgentSessionEvent.session_id == record.id, AgentSessionEvent.type == "session.opened")
                .order_by(AgentSessionEvent.seq)
            )
        )
        .scalars()
        .first()
    )
    return dict(payload or {})


async def _relayed(session: AsyncSession, record: AgentSessionRecord) -> Spend | None:
    """What a voice session's relay priced, summed from its journal: what a relay that died left
    there is booked by the sweep the same way."""
    from .realtime_spend import relayed_spend

    payloads = (
        await session.execute(
            select(AgentSessionEvent.payload).where(
                AgentSessionEvent.session_id == record.id, AgentSessionEvent.type == "response.done"
            )
        )
    ).scalars()
    return relayed_spend(list(payloads), record.model)


async def sweep_expired(session: AsyncSession, project_id: str) -> int:
    """Closes the project's sessions past their expiry: their spend is booked, their keys revoked."""
    rows = (
        await session.execute(
            select(AgentSessionRecord).where(
                AgentSessionRecord.project_id == project_id,
                AgentSessionRecord.kind == "session",
                AgentSessionRecord.status == "open",
                AgentSessionRecord.expires_at <= utcnow(),
            )
        )
    ).scalars()
    closed = 0
    for row in list(rows):
        await close_session(session, row, reason="expired")
        closed += 1
    return closed


# ───────────────────────────── the journal ─────────────────────────────


async def journal(
    session: AsyncSession, record: AgentSessionRecord, type_: str, payload: dict[str, Any]
) -> None:
    """One event of the transcript, numbered in order: text only, never audio."""
    last = (
        await session.execute(
            select(func.coalesce(func.max(AgentSessionEvent.seq), 0)).where(
                AgentSessionEvent.session_id == record.id
            )
        )
    ).scalar_one()
    session.add(
        AgentSessionEvent(
            org_id=record.org_id,
            session_id=record.id,
            seq=int(last) + 1,
            type=type_,
            payload=payload,
            ts=utcnow(),
        )
    )
    await session.flush()


async def session_dto(session: AsyncSession, record: AgentSessionRecord, project_slug: str) -> AgentSession:
    events = (
        await session.execute(
            select(AgentSessionEvent)
            .where(AgentSessionEvent.session_id == record.id)
            .order_by(AgentSessionEvent.seq)
        )
    ).scalars()
    return AgentSession(
        id=record.id,
        agent=record.agent_slug,
        agent_version=record.agent_version,
        project=project_slug,
        status=record.status,
        reason=record.reason,
        model=record.model,
        responses=record.responses,
        tool_calls=record.tool_calls,
        cost_usd=record.cost_usd,
        created_at=record.created_at,
        expires_at=record.expires_at,
        closed_at=record.closed_at,
        events=[
            AgentSessionEventDto(seq=e.seq, type=e.type, ts=e.ts, payload=e.payload or {}) for e in events
        ],
    )


__all__ = [
    "DEFAULT_SESSION_USD",
    "SESSION_TTL_S",
    "SessionToolAnswer",
    "answer_session_tool",
    "budget_refusal",
    "call_session_tool",
    "close_session",
    "journal",
    "mint_key",
    "open_session",
    "reserve_budget",
    "respond",
    "secret_name",
    "session_dto",
    "session_gateway",
    "session_tools",
    "set_model_transport",
    "set_session_gateway",
    "sweep_expired",
    "tool_parameters",
]
