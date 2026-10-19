# SPDX-License-Identifier: Apache-2.0
"""Remote agents over A2A (ADR 0062): what the orchestrator decides, the runner executes.

The orchestrator never calls the remote agent: it prepares the step (the `RemoteCall`, the declared
cost to reserve, the egress and credential checks), mints the short-lived access token for the run's
Secret at `start_run`, and books the declared cost at the end. The pod makes the call.

Every refusal here is `AgentIndisponible`, non-retryable and one of the interpreter's configuration
refusals: the work item is escalated with the reason, a person fixes the configuration, the stage
is replayed — and no new error type reaches `workflows/`.
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatch
from typing import Any
from urllib.parse import urlsplit

from choregos_contracts import Budget, DeclaredCost, RemoteCall, StageInput
from choregos_core import network
from temporalio.exceptions import ApplicationError

from ..org_egress import merged_domains, org_runner_hosts

A2A_BACKEND = "a2a"
#: What a remote agent's `Budget.usd` is when nothing is declared: the contract wants a positive
#: figure, the reservation (`reserve_cap`) is what really counts and is zero.
NOMINAL_BUDGET_USD = 0.01


def refuse(message: str) -> ApplicationError:
    return ApplicationError(message, type="AgentIndisponible", non_retryable=True)


def a2a_runtime(agent: Any) -> Any:
    """The `a2a` runtime of the resolved agent version, or `None` for any other."""
    runtime = getattr(getattr(agent, "spec", None), "runtime", None)
    return runtime if runtime is not None and runtime.kind == "a2a" else None


def reserve_cap(runtime: Any, minutes: int) -> float:
    """What a run of this agent may cost at most, by its declaration: per task plus per minute of the
    step's time. Zero when nothing is declared — said plainly in the ledger as `unmeasured`."""
    cost = runtime.declared_cost
    return round((cost.per_task_usd or 0.0) + (cost.per_minute_usd or 0.0) * minutes, 6)


def remote_minutes(runtime: Any, plan_max_minutes: int | None) -> int:
    return (
        min(runtime.timeout_minutes, plan_max_minutes) if plan_max_minutes else int(runtime.timeout_minutes)
    )


def remote_budget(runtime: Any, plan_max_minutes: int | None) -> tuple[Budget, float]:
    """The stage budget (positive by contract) and the amount to reserve against the ticket and the day."""
    minutes = remote_minutes(runtime, plan_max_minutes)
    cap = reserve_cap(runtime, minutes)
    return Budget(usd=max(cap, NOMINAL_BUDGET_USD), max_turns=1, max_minutes=minutes), cap


def host_allowed(host: str, domains: list[str]) -> bool:
    """`allow_domains` entries are exact hosts or `*.example.com` patterns (a bare `example.com` also
    allows its subdomains, as the sandbox's egress does)."""
    host = host.lower()
    for entry in domains:
        pattern = entry.lower().strip()
        if pattern in {"*", host} or fnmatch(host, pattern):
            return True
        if not pattern.startswith("*") and host.endswith("." + pattern):
            return True
    return False


def check_egress(runtime: Any, domains: list[str], deny: Iterable[str] = ()) -> None:
    """Admission: the pod may only reach the hosts the project's policy allows (ADR 0062 §8), and
    never a host a deny list of the instance, the organisation's ceiling or the project names
    (ADR 0070: a deny always wins, over an organisation's opening too)."""
    pinned = runtime.pinned_card
    if pinned is None:
        raise refuse("the remote agent version carries no pinned card: publish it again")
    host = urlsplit(pinned.url).hostname or ""
    if (hit := next((p for p in deny if network.host_matches(host, p)), None)) is not None:
        raise refuse(
            f"the remote agent's host `{host}` is denied by the network policy (`{hit}`): the "
            "organisation's ceiling, the project's sandbox.network.deny_domains or the instance"
        )
    if not host_allowed(host, domains):
        raise refuse(
            f"the remote agent's host `{host}` is not in the project's `sandbox.network.allow_domains`: "
            f"add it to the policy (and to the platform's egress allow-list) before this step can run"
        )


async def check_credential(session: Any, org_id: str, runtime: Any) -> None:
    """A remote agent whose credential is gone, revoked or never authorised does not start."""
    if runtime.credential is None:
        return
    from choregos_api.services.remote_agents import credential_named

    row = await credential_named(session, org_id, runtime.credential)
    if row is None:
        raise refuse(f"the remote credential `{runtime.credential}` no longer exists")
    if row.status in {"revoked", "pending"}:
        raise refuse(
            f"the remote credential `{runtime.credential}` is {row.status}: connect it in the administration"
        )


def remote_call(runtime: Any, slug: str, budget: Budget) -> RemoteCall:
    pinned = runtime.pinned_card
    return RemoteCall(
        endpoint=pinned.url,
        card_url=runtime.card_url,
        protocol_version=pinned.protocol_version,
        skill=runtime.skill,
        card_digest=pinned.digest,
        timeout_s=budget.max_minutes * 60,
        input=runtime.input,
        declared_cost=DeclaredCost(
            per_task_usd=runtime.declared_cost.per_task_usd,
            per_minute_usd=runtime.declared_cost.per_minute_usd,
        ),
        agent_slug=slug,
        credential=runtime.credential,
    )


async def mint_remote_token(session: Any, org_id: str, stage_input: StageInput) -> str | None:
    """The access token for the run's Secret: exchanged from the credential, short-lived, never stored.

    `None` for a step that is not remote, or a card that asks for no authentication."""
    remote = stage_input.remote
    if remote is None or remote.credential is None:
        return None
    from choregos_api.services.remote_tokens import RemoteCredentialError, access_token_for

    try:
        access = await access_token_for(session, org_id, remote.credential)
    except RemoteCredentialError as exc:
        raise refuse(
            f"the remote agent's credential `{remote.credential}` cannot give a token: {exc}"
        ) from exc
    return access.token or None


def declared_cost_usd(stage_input: StageInput, seconds: float) -> float:
    """What the run is booked: the per-task declaration plus the per-minute one for the wall time."""
    remote = stage_input.remote
    if remote is None:
        return 0.0
    cost = remote.declared_cost
    return round((cost.per_task_usd or 0.0) + (cost.per_minute_usd or 0.0) * max(seconds, 0.0) / 60.0, 6)


async def check_remote_agent(
    session: Any,
    bundle: Any,
    runtime: Any,
    *,
    deny: Iterable[str] = (),
    org_hosts: list[str] | None = None,
) -> None:
    """The two admission checks of a remote agent; nothing for any other runtime. `deny` is the run's
    effective deny list (`run_network(...).deny`)."""
    if runtime is None:
        return
    # The project's own allowlist, plus what the organisation opened for its runners (S39-22d).
    if org_hosts is None:
        org_hosts = await org_runner_hosts(session, bundle.project.org_id)
    check_egress(runtime, merged_domains(bundle.engine.allow_domains(), org_hosts), deny)
    await check_credential(session, bundle.project.org_id, runtime)
