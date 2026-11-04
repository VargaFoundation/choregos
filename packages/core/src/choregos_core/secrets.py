# SPDX-License-Identifier: Apache-2.0
"""Secret references (ADR 0034): the `SecretResolver` seam.

A connector never carries a secret in clear: its configuration names a REFERENCE
(`env:CONNECTOR_JIRA_TOKEN`), which the platform resolves when it builds the adapter, in the process
that needs it, never in the database, never in an API response. The core reads the environment
(`env:`); a plugin declares the other schemes (a vault: `vault:`, `infisical:`) with
`declare_resolver`.

`connectors.secret_ref` existed since the initial schema, stored and never read: a "configured"
secret reached no adapter, which silently fell back on its default value.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable

from .compat import EXTERNAL_ALIAS_REMOVAL, alias_names

#: `scheme:rest`: a lowercase scheme, then whatever its resolver can read.
_REFERENCE = re.compile(r"^([a-z][a-z0-9-]{1,31}):(.+)$")


class SecretNotFound(LookupError):  # noqa: N818 - a named absence, answered as it is
    """The reference is well formed, but nothing resolves it here."""


class InvalidReference(ValueError):  # noqa: N818 - a reasoned refusal, answered as 422
    """Not a secret reference that a declared resolver can read."""


class RefusedVariable(InvalidReference):
    """`env:NAME` names a variable outside the allowlist (S39-06b)."""


#: Which variables an `env:NAME` reference may read (S39-06b). A reference is written by a tenant
#: role and resolved by the API or the orchestrator, whose environment also holds the platform's own
#: secrets (session key, gateway master key, database password): a name outside this pattern is
#: refused at write and again at resolution. A deployment widens it with this variable.
PATTERN_ENV = "CHOREGOS_SECRET_ENV_PATTERN"
DEFAULT_PATTERN = r"^CONNECTOR_[A-Z0-9_]+$"

#: Never readable through a reference, whatever the pattern says: the platform's own variables.
_ALWAYS_REFUSED = re.compile(r"^(CHOREGOS_|DATABASE_|PG|AWS_|KUBERNETES_|TEMPORAL_)")


def _pattern() -> str:
    return os.environ.get(PATTERN_ENV) or DEFAULT_PATTERN


def _check_the_name(name: str) -> None:
    if not re.fullmatch(r"[A-Z_][A-Z0-9_]*", name):
        raise InvalidReference(f"`env:{name}`: an UPPERCASE variable name is expected")
    pattern = _pattern()
    try:
        allowed = re.search(pattern, name) is not None
    except re.error as error:
        raise InvalidReference(f"{PATTERN_ENV} is not a valid pattern: {error}") from error
    if _ALWAYS_REFUSED.match(name) or not allowed:
        raise RefusedVariable(f"env:NAME: only variables matching {pattern} can be referenced")


def _environment(name: str) -> str:
    _check_the_name(name)
    value = os.environ.get(name)
    if not value:
        raise SecretNotFound(f"`env:{name}`: the variable is not set in this process")
    return value


#: The scheme of a value typed in the console and sealed in the database (ADR 0058).
STORE_SCHEME = "store"


def _store_is_resolved_by_the_platform(name: str) -> str:
    """The sync core cannot read the store: it needs the database and the organisation. A call site
    that forgot to go through `choregos_api.secret_store` fails loudly here, never silently."""
    raise SecretNotFound(
        f"`store:{name}`: store references are resolved by the platform with the organisation's "
        "context (resolved_configuration), not by the core"
    )


def is_store_reference(reference: str | None) -> bool:
    """Is this a reference to a sealed value (`store:connector/jira/api_token`)?"""
    return bool(reference) and str(reference).startswith(f"{STORE_SCHEME}:")


_RESOLVERS: dict[str, Callable[[str], str]] = {
    "env": _environment,
    STORE_SCHEME: _store_is_resolved_by_the_platform,
}


def declare_resolver(schema: str, resolve_: Callable[[str], str]) -> None:
    """Called by an extension when it loads: `schema` (`vault`, `infisical`...) and its reader."""
    if not re.fullmatch(r"[a-z][a-z0-9-]{1,31}", schema):
        raise ValueError(f"invalid secret scheme: {schema!r}")
    if schema in _RESOLVERS and _RESOLVERS[schema] is not resolve_:
        raise ValueError(f"secret scheme already declared: {schema}")
    _RESOLVERS[schema] = resolve_


def schemas() -> frozenset[str]:
    return frozenset(_RESOLVERS)


def check(reference: str) -> None:
    """Refuse what is not a reference readable here, without resolving it: the API that
    records it does not necessarily hold the secret, the orchestrator that uses it does."""
    m = _REFERENCE.match(reference or "")
    if m is None:
        raise InvalidReference(
            "a secret reference is expected (`env:NAME`), not a value: a secret is never written in clear"
        )
    if m.group(1) not in _RESOLVERS:
        raise InvalidReference(
            f"unknown secret scheme: `{m.group(1)}` (known: {', '.join(sorted(_RESOLVERS))})"
        )
    if m.group(1) == "env":
        _check_the_name(m.group(2))


def resolve(reference: str) -> str:
    try:
        check(reference)
    except RefusedVariable as refused:
        # A reference written before the allowlist (or by hand in the database) puts its connector
        # in error, like any reference nothing resolves: it never reads the variable.
        raise SecretNotFound(str(refused)) from refused
    m = _REFERENCE.match(reference)
    assert m is not None
    return _RESOLVERS[m.group(1)](m.group(2))


def reset() -> None:
    """For tests: only the core's resolvers remain."""
    _RESOLVERS.clear()
    _RESOLVERS["env"] = _environment
    _RESOLVERS[STORE_SCHEME] = _store_is_resolved_by_the_platform


__all__ = [
    "STORE_SCHEME",
    "InvalidReference",
    "RefusedVariable",
    "SecretNotFound",
    "check",
    "declare_resolver",
    "is_store_reference",
    "reset",
    "resolve",
    "schemas",
]

ReferenceInvalide = InvalidReference  # lang: fr-ok alias:internal
VariableRefusee = RefusedVariable  # lang: fr-ok alias:internal
PATTERN_PAR_DEFAUT = DEFAULT_PATTERN  # lang: fr-ok alias:internal
est_une_reference_du_magasin = is_store_reference  # lang: fr-ok alias:internal
verifier = check  # lang: fr-ok alias:internal
resoudre = resolve  # lang: fr-ok alias:internal
reinitialiser = reset  # lang: fr-ok alias:internal
SecretIntrouvable = SecretNotFound  # lang: fr-ok alias:external
declarer_un_resolveur = declare_resolver  # lang: fr-ok alias:external

alias_names(
    __name__,
    {
        "SecretIntrouvable": "SecretNotFound",  # lang: fr-ok alias:external
        "declarer_un_resolveur": "declare_resolver",  # lang: fr-ok alias:external
    },
    removed_in=EXTERNAL_ALIAS_REMOVAL,
)
