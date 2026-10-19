# SPDX-License-Identifier: Apache-2.0
"""An organisation's policy ceiling: what a project policy may only tighten (S27-05).

The same rule as an agent version's project overrides (`services/agents.py`, `elargissements`):
below the ceiling, a project chooses; above it, it does not. A ceiling names caps — money, turns,
minutes, attempts, tool calls, scope sizes —; a cap it does not name leaves the project free.

The comparison is on the values the engine APPLIES (`PolicyEngine`), not on what the YAML happens
to write: a project policy that omits `ticket_usd` gets the platform's defaults, and those are
compared too. A per-role table's `default` cap holds for every role the ceiling does not name.

`widenings()` says what a policy widens — the API refuses it (422), naming each field. `clamp()`
lowers a policy to the ceiling — for project policies saved before a ceiling was set: the policy
that runs is the clamped one, and the console says what was clamped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Literal

from choregos_contracts import Policy
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .network import covered, outside, validate_pattern
from .policy import DEFAULT_STAGE_USD, DEFAULT_TICKET_USD, PolicyEngine


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CeilingBudgets(_Strict):
    ticket_usd: dict[str, float] = Field(default_factory=dict)
    stage_usd: dict[str, dict[str, float]] = Field(default_factory=dict)
    max_turns: dict[str, int] = Field(default_factory=dict)
    max_minutes: dict[str, int] = Field(default_factory=dict)
    tool_calls_per_run: int | None = Field(default=None, ge=1)
    daily_project_usd: float | None = Field(default=None, gt=0)
    #: What all the organisation's projects may spend in a calendar month (UTC). Not a field of a
    #: project policy, so nothing is clamped: it enters every stage's reservation (S27-11).
    monthly_org_usd: float | None = Field(default=None, gt=0)


class CeilingAttempts(_Strict):
    default_max: int | None = Field(default=None, ge=1)
    by_role: dict[str, int] = Field(default_factory=dict)
    dod_iterations: int | None = Field(default=None, ge=0)


class CeilingScope(_Strict):
    auto_grant_max_files: int | None = Field(default=None, ge=0)
    max_diff_files: int | None = Field(default=None, ge=1)
    max_diff_lines: int | None = Field(default=None, ge=1)


class CeilingFindings(_Strict):
    max_per_run: int | None = Field(default=None, ge=0)


class CeilingNetwork(_Strict):
    """The organisation's network bound (ADR 0070): no project or agent goes beyond it."""

    #: The widest set of hosts a project may allow; `None`: no cap. `["*"]`: any public host.
    allow_domains: list[str] | None = None
    #: Hosts no run may reach, whatever a project or an agent says.
    deny_domains: list[str] = Field(default_factory=list)
    per_host_per_minute: int | None = Field(default=None, ge=1)
    per_run_per_minute: int | None = Field(default=None, ge=1)
    builtin_web_tools: Literal["allow", "deny"] | None = None
    #: Where an operator can reach the organisation, written into the User-Agent of what the
    #: platform fetches for it.
    user_agent_contact: str | None = Field(default=None, max_length=200)

    @field_validator("allow_domains", "deny_domains")
    @classmethod
    def _patterns(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else list(dict.fromkeys(validate_pattern(p) for p in value))


class PolicyCeiling(_Strict):
    """The caps an organisation sets on every project policy."""

    budgets: CeilingBudgets = Field(default_factory=CeilingBudgets)
    attempts: CeilingAttempts = Field(default_factory=CeilingAttempts)
    scope: CeilingScope = Field(default_factory=CeilingScope)
    findings: CeilingFindings = Field(default_factory=CeilingFindings)
    network: CeilingNetwork = Field(default_factory=CeilingNetwork)


@dataclass(frozen=True)
class Excess:
    """One value of a policy above its cap, and how to bring it down."""

    field: str
    value: float | str | None
    cap: float | str | None
    lower: Callable[[Policy], None]
    #: A ready-made sentence, for what is not a number against a number (a host, a setting).
    text: str | None = None

    def message(self) -> str:
        if self.text is not None:
            return f"{self.field}: {self.text}"
        assert isinstance(self.cap, int | float)
        assert not isinstance(self.value, str)
        shown = "unlimited" if self.value is None else _number(self.value)
        return f"{self.field}: {shown} exceeds the organisation's ceiling of {_number(self.cap)}"


def _number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


def _roles(ceiling_roles: list[str], policy_roles: list[str]) -> list[str]:
    """The roles to compare: those the ceiling names, and — under a `default` cap — every role the
    policy names that the ceiling does not, and `default` itself."""
    roles = list(ceiling_roles)
    if "default" in ceiling_roles:
        roles += [role for role in policy_roles if role not in ceiling_roles]
    return list(dict.fromkeys(roles))


# The ways a value is brought down to its cap.


def _set_ticket(size: str, cap: float) -> Callable[[Policy], None]:
    def lower(p: Policy) -> None:
        table = p.budgets.ticket_usd or dict(DEFAULT_TICKET_USD)
        table[size] = cap
        p.budgets.ticket_usd = table

    return lower


def _set_stage(role: str, size: str, cap: float) -> Callable[[Policy], None]:
    def lower(p: Policy) -> None:
        # The role's whole table is written out first: the engine falls back on `default` and
        # on the platform's defaults for what a table omits, and that must not change.
        current = PolicyEngine(p)
        table = dict(p.budgets.stage_usd.get(role) or {})
        for known in {*DEFAULT_STAGE_USD, size}:
            table.setdefault(known, current.stage_usd(role, known))
        table[size] = cap
        p.budgets.stage_usd[role] = table

    return lower


def _set_in_table(section: str, name: str, role: str, cap: float) -> Callable[[Policy], None]:
    def lower(p: Policy) -> None:
        getattr(getattr(p, section), name)[role] = int(cap)

    return lower


def _set_value(section: str, name: str, cap: float) -> Callable[[Policy], None]:
    def lower(p: Policy) -> None:
        setattr(getattr(p, section), name, cap)

    return lower


def _budget_excesses(policy: Policy, ceiling: PolicyCeiling) -> Iterator[Excess]:
    engine = PolicyEngine(policy)
    budgets = ceiling.budgets
    for size, usd in budgets.ticket_usd.items():
        if (value := engine.budget_ticket(size)) > usd:
            yield Excess(f"budgets.ticket_usd.{size}", value, usd, _set_ticket(size, usd))

    for role in _roles(list(budgets.stage_usd), list(policy.budgets.stage_usd)):
        for size, usd in budgets.stage_usd.get(role, budgets.stage_usd.get("default", {})).items():
            if (value := engine.stage_usd(role, size)) > usd:
                yield Excess(f"budgets.stage_usd.{role}.{size}", value, usd, _set_stage(role, size, usd))

    for name, applied in (("max_turns", engine.max_turns), ("max_minutes", engine.max_minutes)):
        caps: dict[str, int] = getattr(budgets, name)
        for role in _roles(list(caps), list(getattr(policy.budgets, name))):
            count = caps.get(role, caps.get("default"))
            if count is not None and (turns := applied(role)) > count:
                yield Excess(
                    f"budgets.{name}.{role}", turns, count, _set_in_table("budgets", name, role, count)
                )

    for name in ("tool_calls_per_run", "daily_project_usd"):
        limit: float | None = getattr(budgets, name)
        current: float | None = getattr(policy.budgets, name) or None  # 0 or None: no limit at all
        if limit is not None and (current is None or current > limit):
            yield Excess(f"budgets.{name}", current, limit, _set_value("budgets", name, limit))


def _other_excesses(policy: Policy, ceiling: PolicyCeiling) -> Iterator[Excess]:
    engine = PolicyEngine(policy)
    attempts = ceiling.attempts
    if attempts.default_max is not None and policy.attempts.default_max > attempts.default_max:
        yield Excess(
            "attempts.default_max",
            policy.attempts.default_max,
            attempts.default_max,
            _set_value("attempts", "default_max", attempts.default_max),
        )
    named = [*attempts.by_role, *(["default"] if attempts.default_max is not None else [])]
    for role in _roles(named, list(policy.attempts.by_role)):
        allowed = attempts.by_role.get(role, attempts.default_max)
        if role != "default" and allowed is not None and (tries := engine.attempts_for(role)) > allowed:
            yield Excess(
                f"attempts.by_role.{role}",
                tries,
                allowed,
                _set_in_table("attempts", "by_role", role, allowed),
            )

    for section, name, bound in (
        ("attempts", "dod_iterations", attempts.dod_iterations),
        ("scope", "auto_grant_max_files", ceiling.scope.auto_grant_max_files),
        ("scope", "max_diff_files", ceiling.scope.max_diff_files),
        ("scope", "max_diff_lines", ceiling.scope.max_diff_lines),
        ("findings", "max_per_run", ceiling.findings.max_per_run),
    ):
        if bound is not None and (value := getattr(getattr(policy, section), name)) > bound:
            yield Excess(f"{section}.{name}", value, bound, _set_value(section, name, bound))


def _drop_domain(domain: str) -> Callable[[Policy], None]:
    def lower(p: Policy) -> None:
        net = p.sandbox.network
        net.allow_domains = [d for d in net.allow_domains if d != domain]

    return lower


def _set_network(name: str, cap: float | str) -> Callable[[Policy], None]:
    def lower(p: Policy) -> None:
        setattr(p.sandbox.network, name, cap)

    return lower


def _network_excesses(policy: Policy, ceiling: PolicyCeiling) -> Iterator[Excess]:
    bound = ceiling.network
    net = policy.sandbox.network
    for domain in net.allow_domains:
        try:
            pattern = validate_pattern(domain)
        except ValueError:
            continue  # a badly written entry matches nothing at run time: not an excess
        if (hit := covered(pattern, bound.deny_domains)) is not None:
            yield Excess(
                "network.allow_domains",
                domain,
                hit,
                _drop_domain(domain),
                f"`{domain}` is denied by the organisation (`{hit}`)",
            )
        elif outside([pattern], bound.allow_domains):
            yield Excess(
                "network.allow_domains",
                domain,
                None,
                _drop_domain(domain),
                f"`{domain}` is outside the organisation's allowed hosts",
            )
    for name in ("per_host_per_minute", "per_run_per_minute"):
        limit: int | None = getattr(bound, name)
        current: int | None = getattr(net, name)
        if limit is not None and (current is None or current > limit):
            yield Excess(f"network.{name}", current, limit, _set_network(name, limit))
    if bound.builtin_web_tools == "deny" and net.builtin_web_tools == "allow":
        yield Excess(
            "network.builtin_web_tools",
            "allow",
            "deny",
            _set_network("builtin_web_tools", "deny"),
            "`allow` is refused, the organisation switches built-in web tools off",
        )


def _excesses(policy: Policy, ceiling: PolicyCeiling) -> Iterator[Excess]:
    yield from _budget_excesses(policy, ceiling)
    yield from _other_excesses(policy, ceiling)
    yield from _network_excesses(policy, ceiling)


def widenings(policy: Policy, ceiling: PolicyCeiling | None) -> list[str]:
    """What this policy sets above the organisation's ceiling, one message per field."""
    if ceiling is None:
        return []
    return [excess.message() for excess in _excesses(policy, ceiling)]


def clamp(policy: Policy, ceiling: PolicyCeiling | None) -> tuple[Policy, list[str]]:
    """The policy brought down to the ceiling, and what was brought down."""
    if ceiling is None:
        return policy, []
    clamped = policy.model_copy(deep=True)
    said: list[str] = []
    # One excess at a time: lowering a `default` may already settle a role that falls back on it.
    for _ in range(200):
        excess = next(_excesses(clamped, ceiling), None)
        if excess is None:
            break
        said.append(excess.message())
        excess.lower(clamped)
    # The organisation's deny list holds for every run: it is merged in, not an excess.
    denied = clamped.sandbox.network.deny_domains
    merged = list(dict.fromkeys([*denied, *ceiling.network.deny_domains]))
    if merged != denied:
        clamped.sandbox.network.deny_domains = merged
    return clamped, said


__all__ = ["CeilingNetwork", "PolicyCeiling", "clamp", "widenings"]
