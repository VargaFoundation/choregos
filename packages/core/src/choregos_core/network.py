# SPDX-License-Identifier: Apache-2.0
"""Network access: host patterns and the effective network of a run (ADR 0070).

One grammar, one implementation: the API, the orchestrator, the runner and the egress proxy all
import this module, so a host is allowed or refused the same way everywhere.

Patterns
--------
- `example.com`   the host and every subdomain of it (the semantics of the runner's guardrail and
  of the Squid `dstdomain` rule);
- `*.example.com` the subdomains only, not the apex;
- `*`             any public host NAME. An IP literal is never matched by anything: a deny list
  keyed on names must not be bypassed with an address.

No scheme, port, path or user-info. A deny always wins over an allow.

Three scopes only ever narrow one another: the organisation's ceiling, the project's policy, the
agent's version. `effective_network()` computes what a run may reach from the three.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

_LABEL_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz0123456789-_")


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return True


def validate_pattern(pattern: str) -> str:
    """The normalised pattern (lower case, no trailing dot), or `ValueError` saying why not."""
    raw = pattern
    value = pattern.strip().lower().rstrip(".")
    if not value:
        raise ValueError("a host pattern cannot be empty")
    if value == "*":
        return value
    for bad, why in (
        ("://", "a scheme"),
        ("/", "a path"),
        ("@", "user-info"),
        (":", "a port"),
        (" ", "a space"),
    ):
        if bad in value:
            raise ValueError(f"`{raw}` is not a host pattern: it carries {why}")
    if _is_ip(value):
        raise ValueError(f"`{raw}` is an IP address: write the host name")
    body = value[2:] if value.startswith("*.") else value
    if "*" in body:
        raise ValueError(f"`{raw}`: `*` is only allowed alone or as a leading `*.`")
    labels = body.split(".")
    if value.startswith("*.") and len(labels) < 2:
        raise ValueError(f"`{raw}`: `*.` needs a domain with at least two labels after it")
    for label in labels:
        if not label:
            raise ValueError(f"`{raw}` has an empty label")
        if not set(label) <= _LABEL_CHARS:
            raise ValueError(f"`{raw}` has a character a host name cannot carry")
    return value


def normalise_host(host: str) -> str:
    return host.strip().lower().rstrip(".")


def host_matches(host: str, pattern: str) -> bool:
    """Whether a host NAME falls under a pattern. An IP literal matches nothing."""
    name = normalise_host(host)
    if not name or _is_ip(name):
        return False
    if pattern == "*":
        return True
    if pattern.startswith("*."):
        return name.endswith(pattern[1:])
    return name == pattern or name.endswith("." + pattern)


def allowed(host: str, allow: Iterable[str], deny: Iterable[str] = ()) -> tuple[bool, str]:
    """Whether a host may be reached, and why: `denied:<p>`, `not_allowed`, `ip_literal` or
    `allowed:<p>`. The deny list is read first."""
    name = normalise_host(host)
    if _is_ip(name):
        return False, "ip_literal"
    for pattern in deny:
        if host_matches(name, pattern):
            return False, f"denied:{pattern}"
    for pattern in allow:
        if host_matches(name, pattern):
            return True, f"allowed:{pattern}"
    return False, "not_allowed"


def pattern_within(inner: str, outer: str) -> bool:
    """Whether every host `inner` matches is also matched by `outer` (so `inner` only narrows)."""
    if outer == "*":
        return True
    if inner == "*":
        return False
    inner_apex = inner[2:] if inner.startswith("*.") else inner
    if outer.startswith("*."):
        base = outer[2:]
        if inner.startswith("*."):
            return inner_apex == base or inner_apex.endswith("." + base)
        return inner_apex.endswith("." + base)
    return inner_apex == outer or inner_apex.endswith("." + outer)


def covered(pattern: str, others: Iterable[str]) -> str | None:
    """The first of `others` that covers `pattern` entirely, or `None`."""
    return next((o for o in others if pattern_within(pattern, o)), None)


def outside(patterns: Iterable[str], outer: Sequence[str] | None) -> list[str]:
    """The patterns not covered by `outer` (`None`: no cap, nothing is outside)."""
    if outer is None:
        return []
    return [p for p in patterns if covered(p, outer) is None]


def _valid(patterns: Iterable[str]) -> list[str]:
    """Patterns normalised, those a stored policy wrote badly dropped (the API refuses new ones)."""
    out: list[str] = []
    for pattern in patterns:
        try:
            out.append(validate_pattern(pattern))
        except ValueError:
            continue
    return list(dict.fromkeys(out))


class NetworkLike(Protocol):
    """A project's `sandbox.network` (the contract's `NetworkPolicy`)."""

    @property
    def allow_domains(self) -> list[str]: ...
    @property
    def deny_domains(self) -> list[str]: ...
    @property
    def per_host_per_minute(self) -> int | None: ...
    @property
    def per_run_per_minute(self) -> int | None: ...
    @property
    def builtin_web_tools(self) -> str: ...


class AgentNetworkLike(Protocol):
    """An agent version's `network`."""

    @property
    def domains(self) -> list[str] | None: ...
    @property
    def per_host_per_minute(self) -> int | None: ...
    @property
    def builtin_web_tools(self) -> bool | None: ...


class CeilingNetworkLike(Protocol):
    """The organisation ceiling's `network` section."""

    @property
    def allow_domains(self) -> list[str] | None: ...
    @property
    def deny_domains(self) -> list[str]: ...
    @property
    def per_host_per_minute(self) -> int | None: ...
    @property
    def per_run_per_minute(self) -> int | None: ...
    @property
    def builtin_web_tools(self) -> str | None: ...


def _least(*values: int | None) -> int | None:
    present = [v for v in values if v is not None]
    return min(present) if present else None


@dataclass(frozen=True)
class EffectiveNetwork:
    """What one run may reach: `allow` and `implied` are allowed, `deny` always wins."""

    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)
    implied: list[str] = field(default_factory=list)
    per_host_per_minute: int | None = None
    per_run_per_minute: int | None = None
    org_per_host_per_minute: int | None = None
    builtin_web_tools: bool = True

    @property
    def reachable(self) -> list[str]:
        """Every allowed pattern, the implied hosts included."""
        return list(dict.fromkeys([*self.allow, *self.implied]))


def effective_network(
    policy_net: NetworkLike | None,
    agent_net: AgentNetworkLike | None,
    ceiling_net: CeilingNetworkLike | None,
    *,
    implied_hosts: Iterable[str] = (),
    instance_deny: Iterable[str] = (),
) -> EffectiveNetwork:
    """The network of a run: the agent's hosts (or the project's) cut down to what the project and
    the organisation allow, the deny lists of the instance, the organisation and the project
    merged, the lowest rate and the strictest built-in web tools setting."""
    deny = _valid(
        [
            *instance_deny,
            *(ceiling_net.deny_domains if ceiling_net else ()),
            *(policy_net.deny_domains if policy_net else ()),
        ]
    )
    cap = (
        None
        if ceiling_net is None or ceiling_net.allow_domains is None
        else _valid(ceiling_net.allow_domains)
    )
    project = _valid(policy_net.allow_domains) if policy_net else []
    project = [p for p in project if p not in outside(project, cap)]
    if agent_net is not None and agent_net.domains is not None:
        wanted = _valid(agent_net.domains)
        # An agent host the project or the organisation does not allow is refused at admission
        # (`unsatisfied`); here it is simply never part of the run's network.
        chosen = [p for p in wanted if covered(p, project)]
    else:
        chosen = project
    chosen = [p for p in chosen if covered(p, deny) is None]
    implied = [h for h in _valid(implied_hosts) if allowed(h, [], deny)[1] == "not_allowed"]
    web = True
    if ceiling_net is not None and ceiling_net.builtin_web_tools == "deny":
        web = False
    if policy_net is not None and policy_net.builtin_web_tools == "deny":
        web = False
    if agent_net is not None and agent_net.builtin_web_tools is False:
        web = False
    return EffectiveNetwork(
        allow=chosen,
        deny=deny,
        implied=implied,
        per_host_per_minute=_least(
            policy_net.per_host_per_minute if policy_net else None,
            agent_net.per_host_per_minute if agent_net else None,
            ceiling_net.per_host_per_minute if ceiling_net else None,
        ),
        per_run_per_minute=_least(
            policy_net.per_run_per_minute if policy_net else None,
            ceiling_net.per_run_per_minute if ceiling_net else None,
        ),
        org_per_host_per_minute=ceiling_net.per_host_per_minute if ceiling_net else None,
        builtin_web_tools=web,
    )


def unsatisfied(
    agent_net: AgentNetworkLike | None,
    policy_net: NetworkLike | None,
    ceiling_net: CeilingNetworkLike | None,
    *,
    implied_hosts: Iterable[str] = (),
    instance_deny: Iterable[str] = (),
) -> list[str]:
    """The hosts an agent asks for that its project or the organisation does not allow: each one
    with the reason, in the words an operator reads. Without a project policy (`None`: the version
    is being published for the organisation, no project yet) only the organisation is checked."""
    if agent_net is None or not agent_net.domains:
        return []
    deny = _valid([*instance_deny, *(ceiling_net.deny_domains if ceiling_net else ())])
    project_allow = _valid(policy_net.allow_domains) if policy_net else []
    project_deny = _valid(policy_net.deny_domains) if policy_net else []
    cap = (
        None
        if ceiling_net is None or ceiling_net.allow_domains is None
        else _valid(ceiling_net.allow_domains)
    )
    reachable_by_project = [*project_allow, *_valid(implied_hosts)]
    lines: list[str] = []
    for raw in agent_net.domains:
        try:
            pattern = validate_pattern(raw)
        except ValueError as error:
            lines.append(str(error))
            continue
        if (hit := covered(pattern, deny)) is not None:
            lines.append(f"`{pattern}` is denied by the organisation or the instance (`{hit}`)")
        elif outside([pattern], cap):
            lines.append(f"`{pattern}` is outside the organisation's allowed hosts")
        elif policy_net is None:
            continue
        elif (hit := covered(pattern, project_deny)) is not None:
            lines.append(f"`{pattern}` is denied by the project's policy (`{hit}`)")
        elif covered(pattern, reachable_by_project) is None:
            lines.append(f"`{pattern}` is not in the project's sandbox.network.allow_domains")
    return lines


def repo_host(url: str | None) -> str | None:
    """The host of a repository URL (`https://h/o/r`, `git@h:o/r`, `ssh://git@h/o/r` or `h/o/r`)."""
    if not url:
        return None
    rest = url.strip()
    if "://" in rest:
        rest = rest.split("://", 1)[1]
    rest = rest.split("@", 1)[-1]
    host = rest.split("/", 1)[0].split(":", 1)[0].lower().rstrip(".")
    try:
        return validate_pattern(host)
    except ValueError:
        return None


__all__ = [
    "AgentNetworkLike",
    "CeilingNetworkLike",
    "EffectiveNetwork",
    "NetworkLike",
    "allowed",
    "covered",
    "effective_network",
    "host_matches",
    "normalise_host",
    "outside",
    "pattern_within",
    "repo_host",
    "unsatisfied",
    "validate_pattern",
]
