# SPDX-License-Identifier: Apache-2.0
"""An organisation's egress allowlist, as a pure matcher (S39-22, ADR 0066).

Three layers decide whether the platform may call a host for a tenant:
0. the **organisation's network bounds** (ADR 0070, amended): its policy ceiling's `network` deny
   list (with the instance's) and allowed set. A deny always wins, over an opened host too, in
   every mode: the mode below governs the list, not the bounds;
1. the **organisation's list** (`org_egress_rules`, decided in the app by its administrator);
2. the **deployment ceiling** (`CHOREGOS_EGRESS_CEILING`, what the operator says the cluster's
   network lets through, per pod family) — an `outside` host is refused before it is sent, with
   a message naming the platform change, instead of a silent timeout;
3. the cluster's own network policy, which this module cannot see.

This module does no I/O: the rules are loaded by the API and put in a context variable, which the
guarded HTTP transport (`choregos_adapters.http.egress`) reads on every request.
"""

from __future__ import annotations

import ipaddress
import os
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Literal

from .errors import ChoregosError

#: The pod families that make an outbound call for a tenant.
FAMILIES: tuple[str, ...] = ("api", "workers", "runners")
LIVE_STATUSES: tuple[str, ...] = ("requested", "allowed")
DEFAULT_PORTS: tuple[int, ...] = (443,)

Mode = Literal["enforce", "warn", "off"]
MODES: tuple[str, ...] = ("enforce", "warn", "off")
OrgState = Literal["allowed", "requested", "absent"]
CeilingState = Literal["open", "outside", "unknown"]
Reason = Literal["org_absent", "org_requested", "ceiling_outside", "no_policy_in_context", "network_policy"]


def normalize_host(raw: str, *, allow_wildcard: bool = False) -> str:
    """Lowercase, IDNA-encoded, no trailing dot. `*.` is allowed as the leftmost label only.

    An IP literal is kept as written. Raises `ValueError` on anything that is not a host."""
    host = raw.strip().lower().rstrip(".")
    if not host:
        raise ValueError("an empty host")
    wildcard = host.startswith("*.")
    if wildcard:
        if not allow_wildcard:
            raise ValueError(f"`{raw}`: a wildcard is not allowed here")
        host = host[2:]
    if _is_ip(host):
        if wildcard:
            raise ValueError(f"`{raw}`: a wildcard does not apply to an IP address")
        return host
    if any(c in host for c in "*/@: "):
        raise ValueError(f"`{raw}` is not a host name")
    try:
        encoded = host.encode("idna").decode("ascii")
    except UnicodeError as error:
        raise ValueError(f"`{raw}` is not a valid host name") from error
    return f"*.{encoded}" if wildcard else encoded


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def host_matches(pattern: str, host: str) -> bool:
    """`*.x.y` matches `a.x.y` and `a.b.x.y`, never `x.y` nor `evilx.y`; anything else is exact.

    `host` may itself be a `*.` pattern (a connector that reaches every subdomain of a domain): it
    is then covered only by an equal pattern, or by a wider wildcard."""
    if pattern.startswith("*."):
        suffix = pattern[1:]
        if host.startswith("*."):
            return host[1:].endswith(suffix)
        return host.endswith(suffix) and len(host) > len(suffix)
    return pattern == host


@dataclass(frozen=True, slots=True)
class AllowedRule:
    """One live rule of the organisation's list."""

    host: str
    ports: tuple[int, ...] = DEFAULT_PORTS
    used_by: tuple[str, ...] = ("api",)

    def covers(self, host: str, port: int, family: str) -> bool:
        return family in self.used_by and port in self.ports and host_matches(self.host, host)


@dataclass(frozen=True, slots=True)
class Verdict:
    """What the three-layer check says about one `(host, port, family)`."""

    host: str
    port: int
    family: str
    org: OrgState
    ceiling: CeilingState

    @property
    def ok(self) -> bool:
        return self.org == "allowed" and self.ceiling != "outside"

    @property
    def reason(self) -> Reason | None:
        if self.org == "absent":
            return "org_absent"
        if self.org == "requested":
            return "org_requested"
        if self.ceiling == "outside":
            return "ceiling_outside"
        return None


class EgressRefusedError(ChoregosError):
    """The platform would have called a host its organisation has not opened (or the deployment's
    ceiling excludes, or the organisation's network bounds refuse)."""

    def __init__(
        self, host: str, port: int, family: str, reason: Reason, org: str = "", detail: str = ""
    ) -> None:
        self.host = host
        self.port = port
        self.family = family
        self.reason = reason
        self.org = org
        #: for `network_policy`: what refused it (`denied:<pattern>`, `not_allowed`)
        self.detail = detail
        super().__init__(refusal_message(host, port, family, reason, org, detail))


def refusal_message(
    host: str, port: int, family: str, reason: Reason, org: str = "", detail: str = ""
) -> str:
    where = f"organisation {org}" if org else "this organisation"
    target = f"{host}:{port}" if port != 443 else host
    if reason == "network_policy":
        return (
            f"egress refused: `{target}` is refused by the network policy of {where} "
            f"({detail or 'denied'}) — an administrator changes its network access on the "
            "Administration page"
        )
    if reason == "org_requested":
        return (
            f"egress refused: `{target}` is requested in {where} but not approved yet — an "
            "administrator decides it in /admin/egress"
        )
    if reason == "ceiling_outside":
        return (
            f"egress refused: `{target}` is outside the platform's declared network for the {family} "
            "(ceiling) — the platform team must open it, see /admin/egress for the change"
        )
    if reason == "no_policy_in_context":
        return f"egress refused: no egress policy in context for a call to `{target}` (a bug: report it)"
    return (
        f"egress refused: `{target}` is not allowed in {where} — an administrator allows it in /admin/egress"
    )


@dataclass(frozen=True, slots=True)
class NetworkBounds:
    """The organisation's network bounds (ADR 0070): its ceiling's allowed set (`None` = no cap) and
    the deny lists of the ceiling and the instance, in the host patterns of `choregos_core.network`."""

    allow: tuple[str, ...] | None = None
    deny: tuple[str, ...] = ()

    def refusal(self, host: str) -> str | None:
        """Why the bounds refuse a call to `host` (`denied:<p>`, `not_allowed`), or `None`. A `*.`
        host (a rule, a connector reaching every subdomain) is refused when a deny covers it."""
        from .network import covered, outside, platform_allowed

        if host.startswith("*."):
            if (hit := covered(host, self.deny)) is not None:
                return f"denied:{hit}"
            return "not_allowed" if outside([host], self.allow) else None
        ok, why = platform_allowed(host, self.allow, self.deny)
        return None if ok else why


@dataclass(frozen=True, slots=True)
class EgressPolicy:
    """The organisation's allowlist for one pod family, a ceiling, the enforcement mode, and the
    organisation's network bounds (ADR 0070), which every mode enforces."""

    org: str = ""
    rules: tuple[AllowedRule, ...] = ()
    #: rules still waiting for a decision (they colour the verdict, they never allow)
    requested: tuple[AllowedRule, ...] = ()
    #: family -> host patterns; an empty or absent family is unknown (not checked)
    ceiling: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    mode: Mode = "enforce"
    bounds: NetworkBounds | None = None

    def verdict(self, host: str, port: int = 443, family: str = "api") -> Verdict:
        host = normalize_host(host, allow_wildcard=True)
        if any(rule.covers(host, port, family) for rule in self.rules):
            org: OrgState = "allowed"
        elif any(rule.covers(host, port, family) for rule in self.requested):
            org = "requested"
        else:
            org = "absent"
        patterns = tuple(self.ceiling.get(family, ()))
        if not patterns:
            ceiling: CeilingState = "unknown"
        else:
            ceiling = "open" if any(host_matches(p, host) for p in patterns) else "outside"
        return Verdict(host, port, family, org, ceiling)

    def check(self, host: str, port: int = 443, family: str = "api", origin: str = "") -> Verdict:
        """Raises `EgressRefusedError` in `enforce`; logs and passes in `warn`; passes in `off`. The
        network bounds come first and refuse in every mode: a deny always wins."""
        if self.bounds is not None:
            name = normalize_host(host, allow_wildcard=True)
            if (why := self.bounds.refusal(name)) is not None:
                raise EgressRefusedError(name, port, family, "network_policy", self.org, why)
        verdict = self.verdict(host, port, family)
        if verdict.ok or self.mode == "off":
            return verdict
        reason = verdict.reason
        assert reason is not None
        if self.mode == "warn":
            _log_would_refuse(verdict, origin, reason, self.org)
            return verdict
        raise EgressRefusedError(host, port, family, reason, self.org)


def _log_would_refuse(verdict: Verdict, origin: str, reason: str, org: str) -> None:
    import structlog

    structlog.get_logger("choregos.egress").warning(
        "egress.would_refuse",
        host=verdict.host,
        port=verdict.port,
        family=verdict.family,
        reason=reason,
        origin=origin,
        org=org,
    )


def default_mode(explicit: str | None = None) -> Mode:
    """`CHOREGOS_EGRESS_MODE`, else `enforce` — and `warn` under `CHOREGOS_FAKES=1`, so the fakes
    suites need no rules."""
    value = (explicit or os.environ.get("CHOREGOS_EGRESS_MODE", "")).strip().lower()
    if value in MODES:
        return value  # type: ignore[return-value]
    return "warn" if os.environ.get("CHOREGOS_FAKES", "").strip() in ("1", "true", "yes") else "enforce"


@dataclass(frozen=True, slots=True)
class EgressScope:
    policy: EgressPolicy
    family: str
    origin: str = ""


_SCOPE: ContextVar[EgressScope | None] = ContextVar("choregos_egress_scope", default=None)


def current_scope() -> EgressScope | None:
    return _SCOPE.get()


def push_scope(policy: EgressPolicy, family: str = "api", origin: str = "") -> Token[EgressScope | None]:
    """`use_policy` for a block too long to indent: pair it with `pop_scope` in a `finally`."""
    return _SCOPE.set(EgressScope(policy, family, origin))


def pop_scope(token: Token[EgressScope | None]) -> None:
    _SCOPE.reset(token)


@contextmanager
def use_policy(policy: EgressPolicy, family: str = "api", origin: str = "") -> Iterator[EgressScope]:
    """Every guarded outbound call made inside the block is checked against `policy`."""
    scope = EgressScope(policy, family, origin)
    token = _SCOPE.set(scope)
    try:
        yield scope
    finally:
        _SCOPE.reset(token)


def check_current(host: str, port: int = 443) -> None:
    """The host pre-check for a call that cannot go through the guarded transport (a subprocess)."""
    scope = _SCOPE.get()
    if scope is None:
        if default_mode() == "enforce":
            raise EgressRefusedError(normalize_host(host), port, "api", "no_policy_in_context")
        return
    scope.policy.check(host, port, scope.family, scope.origin)


def ceiling_from(raw: Mapping[str, Iterable[str]] | None) -> dict[str, tuple[str, ...]]:
    """A ceiling read from configuration: unknown families are dropped, patterns normalised."""
    out: dict[str, tuple[str, ...]] = {}
    for family, patterns in (raw or {}).items():
        if family in FAMILIES:
            out[family] = tuple(normalize_host(p, allow_wildcard=True) for p in patterns)
    return out


#: Configuration fields that hold a URL, in connectors and knowledge sources.
URL_FIELDS: tuple[str, ...] = ("url", "base_url", "graph_url", "login_url", "api_url")


def host_port_of(url: object) -> tuple[str, int] | None:
    """The `(host, port)` an `http(s)` URL points to, or `None` for anything else."""
    from urllib.parse import urlsplit

    if not isinstance(url, str) or "://" not in url:
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port or (80 if parts.scheme == "http" else 443)
        host = normalize_host(parts.hostname or "")
    except ValueError:
        return None
    if parts.scheme not in ("http", "https"):
        return None
    return host, port


def hosts_of_config(
    config: Mapping[str, object], defaults: Mapping[str, object] | None = None
) -> list[tuple[str, int]]:
    """The hosts the URL-shaped fields of a connector's or source's configuration point to; a field
    left out falls back to the type's declared default."""
    found: list[tuple[str, int]] = []
    for name in URL_FIELDS:
        hp = host_port_of(config.get(name) or (defaults or {}).get(name))
        if hp and hp not in found:
            found.append(hp)
    return found
