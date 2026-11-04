# SPDX-License-Identifier: Apache-2.0
"""Which edition runs here, and what it allows itself.

Choregos exists in two editions ([ADR 0024](../../../docs/adr/0024-two-editions.md)):

* **community**: this repository, Apache-2.0, **a single organisation**;
* **enterprise**: proprietary, in a separate repository, which unlocks multi-organisation
  *and finishes it* (the thirteen tables still outside RLS, the life cycle of an organisation, the
  per-organisation ceilings).

The core does not know the enterprise edition: it declares itself, at startup, by calling
`declare(ENTERPRISE)`. As long as nobody does, the edition is community, and that is the right
default, because it is the most restrictive.

Deliberately **without a licence key**: the enterprise edition is pulled from a private registry,
and the access control is the `imagePullSecret`. A key signed offline will be the question the day a
customer hosts it themselves (ADR 0024, decision 5).

This module is the extension API of the edition (an entry-point package of the `choregos.plugins`
group is an *extension*). Its French names of before 0.21 keep working until 0.23.0 and warn
(ADR 0098 §3 (f)).
"""

from __future__ import annotations

import re
import warnings
from collections.abc import Iterable
from typing import Any, Final

from choregos_core.compat import EXTERNAL_ALIAS_REMOVAL, alias_names

from . import tenant_sign_in

COMMUNITY: Final = "community"
ENTERPRISE: Final = "enterprise"

#: A dictionary rather than a module variable: mutating a shared state through `global` is exactly
#: what `ruff` refuses, and a named container says better that there is ONE state.
_state: dict[str, object] = {
    "edition": COMMUNITY,
    "features": frozenset[str](),
    "publisher": None,
    "edition_version": None,
    "edition_licence": None,
}

#: The licence the core is distributed under (an SPDX identifier): the LICENSE file at the root.
CORE_LICENCE: Final = "Apache-2.0"

_MAX_PUBLISHER = 80
_MAX_VERSION = 40
_VERSION_FORMAT = re.compile(r"^[0-9A-Za-z.+-]+$")


def declare(
    name: str,
    *,
    features: Iterable[str] | None = None,
    publisher: str | None = None,
    version: str | None = None,
    licence: str | None = None,
    fonctions: Iterable[str] | None = None,  # lang: fr-ok alias:external
) -> None:
    """Called by the enterprise edition when it loads. The core never calls it.

    `publisher` (who publishes the running edition) and `version` (the edition extension's own
    version) are announced by the extension and only rendered by the core: the core never names an
    edition's publisher itself. Both are optional, and refused for the community edition, which has
    neither. `licence` is the licence the edition extension is distributed under, announced the same
    way (a free label such as an SPDX identifier, 1 to 80 characters), shown in the console's About
    panel. `fonctions=` is the former name of `features=`: it still works and warns.
    """
    if fonctions is not None:
        warnings.warn(
            "declare(fonctions=...) is deprecated: use features= "
            f"(removed in {EXTERNAL_ALIAS_REMOVAL}, ADR 0098)",
            DeprecationWarning,
            stacklevel=2,
        )
        if features is not None:
            raise TypeError("declare: fonctions and features are the same argument, pass one")
        features = fonctions
    if name not in (COMMUNITY, ENTERPRISE):
        raise ValueError(f"unknown edition: {name!r}")
    if publisher is not None:
        publisher = publisher.strip()
        if not 1 <= len(publisher) <= _MAX_PUBLISHER or any(ord(c) < 32 or ord(c) == 127 for c in publisher):
            raise ValueError(
                f"publisher must be 1 to {_MAX_PUBLISHER} characters, without control characters"
            )
    if licence is not None:
        licence = licence.strip()
        if not 1 <= len(licence) <= _MAX_PUBLISHER or any(ord(c) < 32 or ord(c) == 127 for c in licence):
            raise ValueError(f"licence must be 1 to {_MAX_PUBLISHER} characters, without control characters")
    if version is not None and (len(version) > _MAX_VERSION or not _VERSION_FORMAT.match(version)):
        raise ValueError(
            f"edition version must match {_VERSION_FORMAT.pattern} (at most {_MAX_VERSION} characters)"
        )
    if name == COMMUNITY and (publisher is not None or version is not None or licence is not None):
        raise ValueError("the community edition has no publisher, no edition version and no licence")
    _state["edition"] = name
    _state["features"] = frozenset(features) if features else frozenset[str]()
    _state["publisher"] = publisher
    _state["edition_version"] = version
    _state["edition_licence"] = licence


#: What the CORE knows how to do with a list of IdP groups: `choregos:<org>:<role>`, and a bare
#: group for the default organisation. One organisation, one prefix.
#:
#: The enterprise edition wants another one: a prefix PER organisation, groups coming from SAML or
#: SCIM, a mapping table editable in the interface (§2.2 of the plan). It declares it here, when
#: its extension loads, instead of forking `routers/auth.py`.
#:
#: Why an injection point and not a `Protocol`: there is ONE substitutable decision, with a stable
#: signature and a caller. A complete `IdentityProvider` protocol would have as many
#: implementations as there are speculations about what the enterprise will want, and the rest of
#: its identity (SAML, SCIM, session revocation) are NEW routes, not substitutions.
_MAPPER: dict[str, Any] = {}


def declare_group_mapper(function: Any) -> None:
    """Replaces the translation of IdP groups into roles per organisation. The core never calls it.

    Expected signature: `(groups: list[str], default_org: str) -> dict[str, Role]`, the same as
    `routers.auth.roles_des_groupes`, which stays the default.

    WARNING: **a replacement mapper must DELEGATE to the core's mapper for what it does not
    understand**, instead of returning an empty dictionary. It replaces the WHOLE translation: a
    mapper that reads only `<org>/<role>` makes the deployment lose its `choregos:<org>:<role>`
    sign-ins, including the bootstrap one and the development one. Found while proving this seam
    with an extension installed for real: `403 ... missing ['varga']` on the creation of an
    organisation, and the cause was not where the message pointed.

        def mine(groups, default_org):
            roles = dict(roles_des_groupes(groups, default_org))   # the core first
            ...                                                    # then the house forms
            return roles
    """
    _MAPPER["function"] = function


def group_mapper(default: Any) -> Any:
    """The mapper in service: the one an extension declared, otherwise the core's."""
    return _MAPPER.get("function") or default


def current() -> str:
    return str(_state["edition"])


def features() -> frozenset[str]:
    value = _state["features"]
    assert isinstance(value, frozenset)
    return value


def publisher() -> str | None:
    """Who publishes the running edition, as announced by its extension; `None` in the community edition."""
    value = _state["publisher"]
    return value if isinstance(value, str) else None


def edition_version() -> str | None:
    """The edition extension's own version, as announced by it; `None` in the community edition."""
    value = _state["edition_version"]
    return value if isinstance(value, str) else None


def edition_licence() -> str | None:
    """The licence of the edition extension, as announced by it; `None` in the community edition."""
    value = _state["edition_licence"]
    return value if isinstance(value, str) else None


def is_enterprise() -> bool:
    return current() == ENTERPRISE


class SessionRefused(Exception):  # noqa: N818 - a decision, not an error
    """A session validation refuses: `str()` is the reason, rendered as a 401."""


#: The session validations declared by the extensions, and the authorities that designate a
#: platform administrator. Two IDENTITY seams, like the group mapper.
_SESSION_VALIDATIONS: list[Any] = []
_PLATFORM_ADMINISTRATORS: list[Any] = []


def declare_session_validation(function: Any) -> None:
    """A signed session is valid until `exp`: the core cannot revoke it before.

    An extension that wants to (server-side revocation, SCIM deprovisioning) declares here
    `function(session_db, user, payload)`, synchronous or asynchronous, played at EVERY request
    authenticated by cookie. It raises `SessionRefused(reason)` to refuse (401 with the reason).
    Any other exception comes up as it is: a validation that is down is not an acceptance.
    """
    if function not in _SESSION_VALIDATIONS:
        _SESSION_VALIDATIONS.append(function)


def session_validations() -> tuple[Any, ...]:
    return tuple(_SESSION_VALIDATIONS)


def declare_platform_administrator(function: Any) -> None:
    """The core holds as platform administrator the `org_admin` of ALL the organisations
    (`deps.require_platform_admin`). In multi-organisation, a real platform role is fairer: an
    extension declares here `function(session_db, principal) -> bool`, synchronous or asynchronous.

    ADDITIVE: it can GRANT the right to whoever the core's rule refuses, never take it from whoever
    the rule grants it to, so an installation that loads an extension keeps at least its
    administrators.
    """
    if function not in _PLATFORM_ADMINISTRATORS:
        _PLATFORM_ADMINISTRATORS.append(function)


def platform_administrators() -> tuple[Any, ...]:
    return tuple(_PLATFORM_ADMINISTRATORS)


def reset() -> None:
    """For tests: back to the most restrictive default, and to the core's rules."""
    declare(COMMUNITY)
    _MAPPER.pop("function", None)
    _SESSION_VALIDATIONS.clear()
    _PLATFORM_ADMINISTRATORS.clear()
    tenant_sign_in.reset()


# ───────────────────────────── the former names (removed in 0.23.0) ─────────────────────────────

_FORMER = {
    "COMMUNAUTAIRE": "COMMUNITY",  # lang: fr-ok alias:external
    "ENTREPRISE": "ENTERPRISE",  # lang: fr-ok alias:external
    "declarer": "declare",  # lang: fr-ok alias:external
    "declarer_le_mappeur_de_groupes": "declare_group_mapper",  # lang: fr-ok alias:external
    "mappeur_de_groupes": "group_mapper",  # lang: fr-ok alias:external
    "courante": "current",  # lang: fr-ok alias:external
    "fonctions": "features",  # lang: fr-ok alias:external
    "est_entreprise": "is_enterprise",  # lang: fr-ok alias:external
    "SessionRefusee": "SessionRefused",  # lang: fr-ok alias:external
    "declarer_une_validation_de_session": "declare_session_validation",  # lang: fr-ok alias:external
    "validations_de_session": "session_validations",  # lang: fr-ok alias:external
    "declarer_un_administrateur_de_plateforme": (  # lang: fr-ok alias:external
        "declare_platform_administrator"
    ),
    "administrateurs_de_plateforme": "platform_administrators",  # lang: fr-ok alias:external
    "reinitialiser": "reset",  # lang: fr-ok alias:external
}
COMMUNAUTAIRE = COMMUNITY  # lang: fr-ok alias:external
ENTREPRISE = ENTERPRISE  # lang: fr-ok alias:external
declarer = declare  # lang: fr-ok alias:external
declarer_le_mappeur_de_groupes = declare_group_mapper  # lang: fr-ok alias:external
mappeur_de_groupes = group_mapper  # lang: fr-ok alias:external
courante = current  # lang: fr-ok alias:external
fonctions = features  # lang: fr-ok alias:external
est_entreprise = is_enterprise  # lang: fr-ok alias:external
SessionRefusee = SessionRefused  # lang: fr-ok alias:external
declarer_une_validation_de_session = declare_session_validation  # lang: fr-ok alias:external
validations_de_session = session_validations  # lang: fr-ok alias:external
declarer_un_administrateur_de_plateforme = declare_platform_administrator  # lang: fr-ok alias:external
administrateurs_de_plateforme = platform_administrators  # lang: fr-ok alias:external
reinitialiser = reset  # lang: fr-ok alias:external

alias_names(__name__, _FORMER, removed_in=EXTERNAL_ALIAS_REMOVAL)
