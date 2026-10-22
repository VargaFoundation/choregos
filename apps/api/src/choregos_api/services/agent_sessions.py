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

Closing (by its person, at its expiry, or when the gateway refuses the key for budget) reads the
key's spend, books it in the ledger with `session_id`, revokes the key and forgets its secret. It is
idempotent: a session closed twice is closed once.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

import httpx
from choregos_adapters.registry import fakes_enabled
from choregos_contracts import EventType, ProjectConfig
from choregos_core import GatewayModel, ModelResolutionError, ModelResolver, Spend, utcnow
from choregos_core.domain import aware
from choregos_core.prompt_loop import LoopOutcome, LoopRequest, LoopTool, run_loop
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
from .evenements import persist_event

if TYPE_CHECKING:
    from choregos_adapters.base import GatewayAdapter

    from ..rbac import Principal
    from ..schemas import AgentSpec
    from .courtier import OutilDuCourtier

log = get_logger(__name__)

#: How long a session lives: its key's TTL, and when the next open in its project sweeps it.
SESSION_TTL_S = 3600
#: What a session may spend when its caller names no cap (USD); the project's budgets still bind.
DEFAULT_SESSION_USD = 1.0
#: A session's turns per response when its agent names none.
DEFAULT_MAX_TURNS = 20
#: The `gateway_keys.run_id` of a session's key.
KEY_PREFIX = "session:"

SessionRuntime = Literal["prompt", "voice"]
_DOORS: dict[str, str] = {"prompt": "/responses", "voice": "/realtime"}
#: The runtimes each door serves: a hosted agent answers on `/responses` too, from its pod (S41-11).
_SERVED: dict[str, set[str]] = {"prompt": {"prompt", "hosted"}, "voice": {"voice"}}
#: The reasons a session ends that are not failures.
_CLEAN_ENDS = {"closed", "expired", "max_minutes", "idle"}

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
) -> AgentSessionRecord:
    """A new session of the agent in the project: its version, its model, its reservation, its key.

    `runtime` is the door it opens at: `prompt` on `/responses`, `voice` on `/realtime` (S41-10)."""
    from .agents import AgentIndisponible, resoudre_l_agent

    await sweep_expired(session, project.id)
    try:
        resolved = await resoudre_l_agent(session, organisation.id, project.id, agent_slug)
    except AgentIndisponible as refusal:
        raise conflict(str(refusal)) from refusal
    spec = resolved.spec
    kind = spec.runtime.kind if spec.runtime is not None else None
    if kind not in _SERVED[runtime]:
        raise unprocessable(
            f"`{agent_slug}` is not a {runtime} agent (runtime `{kind or 'none'}`): "
            f"only a {' or '.join(sorted(_SERVED[runtime]))} agent answers on `{_DOORS[runtime]}`",
            [{"loc": ["agent", "runtime"], "msg": f"runtime `{kind or 'none'}` has no session here"}],
        )
    if kind == "hosted":
        from .agent_hosted_sessions import hosted_protocol

        if hosted_protocol(spec) is None:
            # Relaying a container's own socket is S41-21's.
            raise unprocessable(
                f"`{agent_slug}` serves only `invocations_ws`: a session reaches a hosted agent by "
                "`responses` or `invocations`",
                [{"loc": ["agent", "runtime", "protocols"], "msg": "no protocol a session speaks"}],
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
    session.add(record)
    await session.flush()
    reservation = await _reserve(session, organisation.id, project, resolved.agent.slug, spec, max_usd)
    from .budgets import DEFAULT_FLOOR_USD

    if reservation.below(DEFAULT_FLOOR_USD):
        raise ApiError(409, "Budget exhausted", f"the session does not open: {reservation.describe()}")

    team_id = None
    from choregos_core.policy import PolicyEngine

    from .definitions import effective_policy

    if (daily := PolicyEngine(await effective_policy(session, project.id)).daily_budget()) is not None:
        team_id = await gateway.ensure_team(f"choregos-{project.id}", daily)
    key = await gateway.mint_key(
        {
            "project": project.slug,
            "agent": record.agent_slug,
            "agent_version": record.agent_version,
            "session_id": record.id,
            "user": principal.user_id,
        },
        budget_usd=reservation.usd,
        ttl_s=ttl_s,
        models=[model.litellm_model],
        team_id=team_id,
    )
    record.gateway_key_id = key.key_id
    session.add(
        GatewayKeyRow(
            key_id=key.key_id,
            run_id=f"{KEY_PREFIX}{record.id}",
            project_id=project.id,
            budget_usd=reservation.usd,
            expires_at=key.expires_at,
        )
    )
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


async def _resolve_model(
    session: AsyncSession, org_id: str, gateway: GatewayAdapter, spec: AgentSpec, config: ProjectConfig
) -> Any:
    from .model_catalogue import load_policy

    resolver = ModelResolver(
        gateway_url=get_settings().gateway_url,
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


async def _reserve(
    session: AsyncSession,
    org_id: str,
    project: Project,
    agent_slug: str,
    spec: AgentSpec,
    max_usd: float | None,
) -> Any:
    """The smallest of what is left under every cap that applies, as `prepare_stage` reads it."""
    from choregos_core.policy import PolicyEngine

    from .agents import depense_du_jour, depense_du_mois
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
        left["agent_day"] = budget.daily_usd - await depense_du_jour(session, org_id, agent_slug)
    if budget.monthly_usd is not None:
        left["agent_month"] = budget.monthly_usd - await depense_du_mois(session, org_id, agent_slug)
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
    return reserve(left)


# ───────────────────────────── responding ─────────────────────────────


async def respond(
    session: AsyncSession, record: AgentSessionRecord, project: Project, inputs: list[str]
) -> AgentResponse:
    """One response of the session: the person's input, the agent's loop, what it said."""
    from .agents import resoudre_l_agent

    if record.status != "open" or aware(record.expires_at) <= utcnow():  # type: ignore[operator]
        if record.status == "open":
            await close_session(session, record, reason="expired")
        raise conflict(f"session {record.id} is {record.status} ({record.reason}): open a new one")
    resolved = await resoudre_l_agent(
        session, record.org_id, project.id, f"{record.agent_slug}@{record.agent_version}"
    )
    spec: AgentSpec = resolved.spec
    runtime = spec.runtime
    if runtime is not None and runtime.kind == "hosted":
        from .agent_hosted_sessions import respond_hosted

        return await respond_hosted(session, record, project, spec, inputs)
    keys = await get_secret_store().get_many(session, record.org_id, [secret_name(record.id)])
    key = keys.get(secret_name(record.id), "")
    tools = await session_tools(session, spec, project)
    messages = list(record.messages or [])
    for text in inputs:
        messages.append({"role": "user", "content": text})
        await journal(session, record, "input.text", {"text": text[:2000]})
    response_id = f"resp_{uuid4().hex}"

    async def complete(body: dict[str, Any]) -> dict[str, Any]:
        settings = get_settings()
        async with httpx.AsyncClient(transport=_OVERRIDES.get("transport"), timeout=300) as http:
            answer = await http.post(
                f"{settings.gateway_url.rstrip('/')}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key}"},
            )
        if answer.status_code != 200:
            raise _ModelRefused(answer.status_code, answer.text)
        parsed = answer.json()
        if not isinstance(parsed, dict):
            raise _ModelRefused(answer.status_code, "the model answered something that is not a completion")
        return parsed

    async def call_tool(name: str, arguments: dict[str, Any]) -> str:
        return await call_session_tool(session, record, project, tools[name], arguments)

    async def on_event(type_: str, payload: dict[str, Any]) -> None:
        await journal(session, record, type_, payload)

    outcome: LoopOutcome | None = None
    error: AgentResponseError | None = None
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
                LoopTool(t.nom, str(t.annonce().get("description") or ""), tool_parameters(t))
                for t in tools.values()
            ],
            complete,
            call_tool,
            on_event=on_event,
        )
    except (_ModelRefused, httpx.HTTPError, ValueError) as exc:
        code = "budget_exceeded" if budget_refusal(str(exc)) else "model_error"
        error = AgentResponseError(code=code, message=str(exc)[:500])
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
    if error is not None and error.code == "budget_exceeded":
        # The key is spent: nothing more can be said in this session.
        await close_session(session, record, reason="budget_exceeded")
    return AgentResponse(
        id=response_id,
        session_id=record.id,
        status=status,
        output=[AgentOutputMessage(content=[AgentOutputText(text=text)])] if text else [],
        output_text=text,
        usage=usage,
        error=error,
    )


def tool_parameters(tool: OutilDuCourtier) -> dict[str, Any]:
    schema = tool.annonce().get("inputSchema")
    return dict(schema) if isinstance(schema, dict) else {"type": "object", "properties": {}}


async def session_tools(
    session: AsyncSession, spec: AgentSpec, project: Project
) -> dict[str, OutilDuCourtier]:
    """The read tools the agent's `mcp_servers` grant in the project: a write or an approval is a
    governed action, which needs a work item, so a session is not offered one."""
    from .courtier import selection_of_spec, tools_of_selection

    granted = await tools_of_selection(session, selection_of_spec(spec.model_dump(mode="json")), project)
    return {name: tool for name, tool in granted.items() if not tool.ecriture and not tool.sous_validation}


async def call_session_tool(
    session: AsyncSession,
    record: AgentSessionRecord,
    project: Project,
    tool: OutilDuCourtier,
    arguments: dict[str, Any],
) -> str:
    """One read through the courtier, as a run's: the policy's cap, the schema, the connector's key
    resolved here, the injection guard, a ledger line. What the model reads back is text."""
    from choregos_core.injection import suspicions as find
    from choregos_core.policy import PolicyEngine

    from .courtier import ArgumentsRefuses, appeler, texte_du_resultat, verifier_les_arguments
    from .definitions import effective_policy

    engine = PolicyEngine(await effective_policy(session, project.id))
    cap = engine.max_tool_calls_per_run()
    if cap and record.tool_calls >= cap:
        return f"tool call cap reached: this session already called {record.tool_calls} tools (cap {cap})"
    try:
        verifier_les_arguments(tool, arguments)
    except ArgumentsRefuses as refusal:
        return str(refusal)
    code, body = await appeler(session, tool, arguments, project=project)
    refused_by_network = isinstance(body, dict) and body.get("type") in {"network_policy", "rate_limited"}
    text = texte_du_resultat(body) if code < 400 else json.dumps(body, default=str)[:2000]
    mode = engine.policy.sandbox.prompt_injection
    found = [] if mode == "ignore" or refused_by_network else find(text, source=f"tool.{tool.nom}")
    if found:
        await persist_event(
            session,
            EventType.SECURITY_INJECTION_SUSPECTED,
            project_id=project.id,
            project_slug=project.slug,
            subject=record.id,
            session_id=record.id,
            tool=tool.nom,
            suspicions=[s.to_dict() for s in found],
        )
        if mode == "block":
            text = f"result of {tool.nom} withheld: suspected injection — a human reviews it"
    if not refused_by_network:
        record.tool_calls += 1
        session.add(
            CostLedger(
                project_id=project.id,
                session_id=record.id,
                ts=utcnow(),
                kind="tool",
                provider=f"mcp:{tool.connecteur.name}",
                model=tool.nom,
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
        tool=tool.nom,
        provider=f"mcp:{tool.connecteur.name}",
        status_code=code,
    )
    return text


# ───────────────────────────── closing ─────────────────────────────


async def close_session(session: AsyncSession, record: AgentSessionRecord, *, reason: str = "closed") -> None:
    """Books the key's spend with `session_id`, revokes the key, forgets its secret. Idempotent."""
    if record.status != "open":
        return
    from .couts import record_cost
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
    "budget_refusal",
    "call_session_tool",
    "close_session",
    "journal",
    "open_session",
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
