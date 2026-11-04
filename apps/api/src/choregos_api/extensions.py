# SPDX-License-Identifier: Apache-2.0
"""The routes an extension adds to the API, without forking `main.py`.

An extension (a `choregos.plugins` entry-point package, loaded by the adapters' extension loader)
could already register a connector, a guarantee, declare an edition or a group mapper. It could not
**serve** anything: `create_app()` only included its own routers. Any feature that needs a route (an
organisation's life cycle, an in-house integration) therefore asked for a patch of the core.

The extension calls `declare_router(router)` from its `register()`; `create_app()` includes it under
`/api/v1`, **after** the core's. The authentication and permission dependencies stay the ones the
router declares itself, as for any core router.

**A route of the extension that overlaps a core route (same path, same method) stops the start.**
Starlette serves the first route that matches: the extension would be ignored without a word, or, if
the order ever changed, would silently replace a core route, an authentication route included. A
replacement is not an addition; it is asked for through a named seam, like the group mapper.

The French names this module had before 0.21 (`greffons.py`, `declarer_un_routeur`...) keep working
until 0.23.0 and warn (ADR 0098 §3 (f)); `docs/development.md` lists them.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from typing import Any

from choregos_core.compat import EXTERNAL_ALIAS_REMOVAL, alias_names
from fastapi import APIRouter
from fastapi.routing import APIRoute, APIWebSocketRoute

_ROUTERS: list[APIRouter] = []


def _former_keyword(current: Any, former: Any, former_name: str, current_name: str, owner: str) -> Any:
    """The value of a keyword that was renamed: the English one, or the former one with a warning."""
    if former is None:
        return current
    warnings.warn(
        f"{owner}({former_name}=...) is deprecated: use {current_name}= "
        f"(removed in {EXTERNAL_ALIAS_REMOVAL}, ADR 0098)",
        DeprecationWarning,
        stacklevel=3,
    )
    if current is not None and current is not former:
        raise TypeError(f"{owner}: {former_name} and {current_name} are the same argument, pass one")
    return former


def declare_router(router: APIRouter) -> None:
    """Called by an extension when it loads. The core never calls it."""
    if not isinstance(router, APIRouter):
        raise TypeError(f"a FastAPI router is expected, not {type(router).__name__}")
    if router not in _ROUTERS:
        _ROUTERS.append(router)


def declared_routers() -> tuple[APIRouter, ...]:
    return tuple(_ROUTERS)


def signatures(router: APIRouter, prefix: str = "") -> set[tuple[str, str]]:
    """{(method, path)} served by a router, prefix included.

    A NESTED router (`include_router` inside the router) is refused rather than skipped: FastAPI
    only exposes it through a private object, and a route one cannot see is a route one cannot
    guarantee overlaps nothing.
    """
    served: set[tuple[str, str]] = set()
    for route in router.routes:
        if isinstance(route, APIWebSocketRoute):  # a socket is served on its path, like a method
            served.add(("WEBSOCKET", prefix + route.path))
            continue
        if not isinstance(route, APIRoute):
            raise TypeError(
                f"route {type(route).__name__} in an extension router: only direct routes "
                "(`@router.get`, `@router.post`...) are supported, not a nested router."
            )
        for method in route.methods or ():
            served.add((method, prefix + route.path))
    return served


def reset() -> None:
    """For tests: no extension router, no gesture check, no tool, no section, no template installer."""
    _ROUTERS.clear()
    _TOOL_PROVIDERS.clear()
    _HUMAN_TOOL_PROVIDERS.clear()
    _SECTIONS.clear()
    _INSTALLERS.clear()
    _THEME_PROVIDER.clear()
    reset_gesture_checks()


# ───────────────────────────── administration sections ─────────────────────────────
#
# An extension that serves administration routes (the enterprise edition: organisations, SAML, SCIM,
# sessions) has no screen of its own. It declares its sections as DATA (a manifest: form, table,
# action, secret shown once), and the console renders them with its own blocks: no extension code
# runs in the page that carries the session and the decisions (ADR 0032). `create_app()` validates
# each manifest and checks that each of its paths is served.

_SECTIONS: list[dict[str, Any]] = []

# What the core's section manifests support, so an extension detects a feature without parsing versions
# (S42-02). `block_permission`: a block may carry its own `permission`. `asset_upload` (S42-13): a form
# property of `format: "asset"` uploads its file to its `x-upload` path. `section_icon` (S42-15): a
# section may name its navigation icon from the schema's closed `icon` vocabulary.
SECTION_FEATURES: frozenset[str] = frozenset({"block_permission", "asset_upload", "section_icon"})


def declare_admin_section(manifest: dict[str, Any]) -> None:
    """Called by an extension when it loads; the validation happens when the API starts."""
    import copy

    if not isinstance(manifest, dict):
        raise TypeError(f"a manifest is an object, not {type(manifest).__name__}")
    for section in _SECTIONS:
        if section.get("id") != manifest.get("id"):
            continue
        # The same extension loaded twice (by the orchestrator the API imports, then by
        # `create_app()`) declares the same section again: nothing to redo, as for a router. ANOTHER
        # section under the same identifier would hide one.
        if section == manifest:
            return
        # The message is pinned by tests that stay unchanged until their own story.
        raise ValueError(f"section d'administration déclarée deux fois : {manifest.get('id')}")  # lang: fr-ok
    _SECTIONS.append(copy.deepcopy(manifest))


def declared_sections() -> tuple[dict[str, Any], ...]:
    return tuple(_SECTIONS)


def _uploads(schema: Any) -> list[tuple[str, str]]:
    """The POST paths of a form schema's `format: "asset"` properties (`x-upload`, S42-13)."""
    if not isinstance(schema, dict):
        return []
    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return []
    return [
        ("POST", str(prop["x-upload"]))
        for prop in properties.values()
        if isinstance(prop, dict) and prop.get("format") == "asset" and "x-upload" in prop
    ]


def section_paths(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """{(method, path)} a section calls: the console calls no other."""
    paths: list[tuple[str, str]] = []

    def action(block: dict[str, Any], default: str = "POST") -> None:
        paths.append((str(block.get("method") or default), str(block["path"])))

    for block in manifest.get("blocks", []):
        kind = block.get("kind")
        if kind == "form":
            paths.append(("GET", str(block["read"])))
            paths.append((str(block.get("write_method") or "PUT"), str(block["write"])))
            paths.extend(_uploads(block.get("schema")))
        elif kind == "table":
            paths.append(("GET", str(block["list"])))
            for row in block.get("row_actions", []):
                action(row)
        elif kind in {"action", "secret_once"}:
            action(block)
    return paths


# ───────────────────────────── tools of the runs ─────────────────────────────
#
# The tool catalogue (`catalogue.py`) describes third-party APIs, declared by the deployment in a
# file. An extension can have its own, which depend on the run's PROJECT and not on the deployment:
# the tools generated from a project's ontology, for example. They go through the same path as the
# catalogue's: `GET /internal/runs/{id}/tools` announces them to the agent's `choregos-tools` MCP
# server, `POST /internal/runs/{id}/tools/{name}` calls them under the same per-run call ceiling,
# with the same cost ledger line and the same event. The run token stays the only authentication: the
# provider receives the run and its project, already verified, never the token.


@dataclass(frozen=True, init=False)
class ToolProvider:
    """`list_tools(session, run, project)` returns tools in the MCP format (`name`, `description`,
    `inputSchema`); `call_tool(session, run, project, name, arguments)` returns `(HTTP code, body)`.
    A code of 400 or more is an answer the agent reads, not a failure.

    A listed tool may carry `result_max_bytes` (an integer of at least 1024): how much of its answer
    the agent reads, as a catalogue entry declares it (S40-17b); the agent is never shown it. It may
    carry `x-choregos-access` (`read`, `write` or `approval`: a call proposes what a person decides),
    which the run's tool list announces (ADR 0122); without it, a tool annotated `readOnlyHint: true`
    is a read and any other a write.

    The keywords `lister` and `appeler` are the former names of `list_tools` and `call_tool`: they
    keep working until 0.23.0 and warn."""

    list_tools: Any
    call_tool: Any

    def __init__(
        self,
        list_tools: Any = None,
        call_tool: Any = None,
        *,
        lister: Any = None,  # lang: fr-ok alias:external
        appeler: Any = None,  # lang: fr-ok alias:external
    ) -> None:
        list_tools = _former_keyword(
            list_tools,
            lister,  # lang: fr-ok alias:external
            "lister",  # lang: fr-ok alias:external
            "list_tools",
            "ToolProvider",
        )
        call_tool = _former_keyword(
            call_tool,
            appeler,  # lang: fr-ok alias:external
            "appeler",  # lang: fr-ok alias:external
            "call_tool",
            "ToolProvider",
        )
        if list_tools is None or call_tool is None:
            raise TypeError("ToolProvider needs list_tools and call_tool")
        object.__setattr__(self, "list_tools", list_tools)
        object.__setattr__(self, "call_tool", call_tool)

    @property
    def lister(self) -> Any:  # lang: fr-ok alias:external
        """Former name of `list_tools`."""
        return self.list_tools

    @property
    def appeler(self) -> Any:  # lang: fr-ok alias:external
        """Former name of `call_tool`."""
        return self.call_tool


_TOOL_PROVIDERS: dict[str, ToolProvider] = {}


def declare_tool_provider(
    name: str,
    list_tools: Any = None,
    call_tool: Any = None,
    *,
    lister: Any = None,  # lang: fr-ok alias:external
    appeler: Any = None,  # lang: fr-ok alias:external
) -> None:
    """Called by an extension when it loads. Two extensions do not declare the same name."""
    provider = ToolProvider(
        list_tools,
        call_tool,
        lister=lister,  # lang: fr-ok alias:external
        appeler=appeler,  # lang: fr-ok alias:external
    )
    if name in _TOOL_PROVIDERS and _TOOL_PROVIDERS[name] != provider:
        raise ValueError(f"tool provider “{name}” already declared")
    _TOOL_PROVIDERS[name] = provider


def tool_providers() -> dict[str, ToolProvider]:
    return dict(_TOOL_PROVIDERS)


# ───────────────────────────── tools served to humans, through the MCP door ─────────────────────────────


@dataclass(frozen=True, init=False)
class HumanToolProvider:
    """The tools an extension serves at the MCP door of external clients (ADR 0030), with the rights
    of the HUMAN who minted the token, never those of a platform agent.

    - `list_tools(session, principal, project, org)` returns tools in the MCP format (`name`,
      `description`, `inputSchema`), plus `write: bool`: a write tool is only announced to an
      `mcp:write` token;
    - `call_tool(session, principal, project, org, name, arguments)` returns `(HTTP code, body)`; a
      code >= 400 is a refusal the model reads;
    - `pending(session, principal, project, org)`, optional, returns what waits for a human decision:
      `[{key, title, kind, question, requested_at, can_decide, decision_path}]`, where
      `decision_path` is the console path where the decision is taken.

    An extension's tool never takes the name of a core tool: the gate would discard it.

    The keywords `lister`, `appeler` and `en_attente` are the former names of `list_tools`,
    `call_tool` and `pending`: they keep working until 0.23.0 and warn.
    """

    list_tools: Any
    call_tool: Any
    pending: Any = None

    def __init__(
        self,
        list_tools: Any = None,
        call_tool: Any = None,
        pending: Any = None,
        *,
        lister: Any = None,  # lang: fr-ok alias:external
        appeler: Any = None,  # lang: fr-ok alias:external
        en_attente: Any = None,  # lang: fr-ok alias:external
    ) -> None:
        owner = "HumanToolProvider"
        list_tools = _former_keyword(
            list_tools,
            lister,  # lang: fr-ok alias:external
            "lister",  # lang: fr-ok alias:external
            "list_tools",
            owner,
        )
        call_tool = _former_keyword(
            call_tool,
            appeler,  # lang: fr-ok alias:external
            "appeler",  # lang: fr-ok alias:external
            "call_tool",
            owner,
        )
        pending = _former_keyword(
            pending,
            en_attente,  # lang: fr-ok alias:external
            "en_attente",  # lang: fr-ok alias:external
            "pending",
            owner,
        )
        if list_tools is None or call_tool is None:
            raise TypeError("HumanToolProvider needs list_tools and call_tool")
        object.__setattr__(self, "list_tools", list_tools)
        object.__setattr__(self, "call_tool", call_tool)
        object.__setattr__(self, "pending", pending)

    @property
    def lister(self) -> Any:  # lang: fr-ok alias:external
        """Former name of `list_tools`."""
        return self.list_tools

    @property
    def appeler(self) -> Any:  # lang: fr-ok alias:external
        """Former name of `call_tool`."""
        return self.call_tool

    @property
    def en_attente(self) -> Any:  # lang: fr-ok alias:external
        """Former name of `pending`."""
        return self.pending


_HUMAN_TOOL_PROVIDERS: dict[str, HumanToolProvider] = {}


def declare_human_tools(
    name: str,
    list_tools: Any = None,
    call_tool: Any = None,
    pending: Any = None,
    *,
    lister: Any = None,  # lang: fr-ok alias:external
    appeler: Any = None,  # lang: fr-ok alias:external
    en_attente: Any = None,  # lang: fr-ok alias:external
) -> None:
    """Called by an extension when it loads. Two extensions do not declare the same name."""
    provider = HumanToolProvider(
        list_tools,
        call_tool,
        pending,
        lister=lister,  # lang: fr-ok alias:external
        appeler=appeler,  # lang: fr-ok alias:external
        en_attente=en_attente,  # lang: fr-ok alias:external
    )
    if name in _HUMAN_TOOL_PROVIDERS and _HUMAN_TOOL_PROVIDERS[name] != provider:
        raise ValueError(f"human tool provider “{name}” already declared")
    _HUMAN_TOOL_PROVIDERS[name] = provider


def human_tool_providers() -> dict[str, HumanToolProvider]:
    return dict(_HUMAN_TOOL_PROVIDERS)


# ───────────────────────────── what a template ships to an extension ─────────────────────────────
#
# A template ships workflows, a policy, agents and skills: the core knows how to install them. It can
# also ship what only an extension understands, an ontology for example:
# `defaults.extensions: {<installer>: <template folder>}`. When the project is born, the core reads
# the folder (never leaving it, no symbolic link) and hands it to the installer declared under that
# name, in the transaction that creates the project: a refusal of the installer (`HTTPException`)
# fails the birth, like an invalid workflow. Without an installer (the extension is not there, or not
# active) the project is born without, and the audit log says so (S20-09).

_INSTALLERS: dict[str, Any] = {}


def declare_template_installer(name: str, installer: Any) -> None:
    """`installer(session, project, files, author)`, asynchronous, returns what it installed (an
    object the audit keeps); `files`: path relative to the folder -> text. Two extensions do not
    declare the same name."""
    if name in _INSTALLERS and _INSTALLERS[name] is not installer:
        # The message is pinned by tests that stay unchanged until their own story.
        raise ValueError(f"installateur de gabarit « {name} » déjà déclaré")  # lang: fr-ok
    _INSTALLERS[name] = installer


def template_installers() -> dict[str, Any]:
    return dict(_INSTALLERS)


# ───────────────────────────── checks of human gestures ─────────────────────────────
#
# Some human gestures carry an operating rule the core does not know: a ceiling of projects per
# organisation, a FRESH authentication to approve a production release. An extension declares a
# check, played by the route BEFORE it acts, after the core's permissions, which stay the first
# judges: a check can only refuse more.

#: The gestures that can be checked, and what the route passes in `target`.
GESTURES = {
    "project.create": "slug of the project to create",
    "workitem.decision": "work item, transition, kind of the request and of the decision",
    "release.approve": "release, environment",
    # Releasing an agent revision to production or to a canary share (S40-02, ADR 0071).
    "agent.release": "agent, channel, version, kind",
}


@dataclass(frozen=True)
class GestureRequest:
    gesture: str
    org: str
    principal: Any
    target: dict[str, Any] = field(default_factory=dict)

    @property
    def geste(self) -> str:  # lang: fr-ok alias:external
        """Former name of `gesture`."""
        return self.gesture

    @property
    def cible(self) -> dict[str, Any]:  # lang: fr-ok alias:external
        """Former name of `target`."""
        return self.target


_NATURES = {"forbidden", "conflict", "reauth"}
#: The natures of the French API: they map to the English ones, with a warning.
_FORMER_NATURES = {"interdit": "forbidden", "conflit": "conflict"}  # lang: fr-ok alias:external


class GestureRefused(Exception):  # noqa: N818 - a decision, not an error
    """`nature` picks the answer: `forbidden` (403), `conflict` (409), `reauth` (401: sign in again
    with `GET /api/v1/auth/login?reauth=1`, then the gesture). `interdit` and `conflit` are the former
    names of the first two: they keep working until 0.23.0 and warn."""

    def __init__(self, reason: str, nature: str = "forbidden") -> None:
        if nature in _FORMER_NATURES:
            current = _FORMER_NATURES[nature]
            warnings.warn(
                f"GestureRefused nature {nature!r} is deprecated: use {current!r} "
                f"(removed in {EXTERNAL_ALIAS_REMOVAL}, ADR 0098)",
                DeprecationWarning,
                stacklevel=2,
            )
            nature = current
        if nature not in _NATURES:
            raise ValueError(f"unknown nature: {nature!r}")
        super().__init__(reason)
        self.nature = nature

    @property
    def raison(self) -> str:  # lang: fr-ok alias:external
        """Former name of the reason, `str(refusal)`."""
        return str(self)


_GESTURE_CHECKS: dict[str, dict[str, Any]] = {gesture: {} for gesture in GESTURES}


def declare_gesture_check(gesture: str, name: str, function: Any) -> None:
    """`function(db_session, request)`, sync or async; raises `GestureRefused` to refuse. Any other
    exception goes up: a check that is down is not an authorisation."""
    if gesture not in GESTURES:
        # The message is pinned by tests that stay unchanged until their own story.
        raise ValueError(f"geste inconnu : {gesture!r} (connus : {sorted(GESTURES)})")  # lang: fr-ok
    _GESTURE_CHECKS[gesture][name] = function


async def check_gesture(session: Any, request: GestureRequest) -> None:
    """Called by the core's routes. Turns the first refusal into an HTTP answer, prefixed by the name."""
    import inspect

    from .errors import conflict, forbidden, unauthorized

    for name, function in _GESTURE_CHECKS[request.gesture].items():
        try:
            result = function(session, request)
            if inspect.isawaitable(result):
                await result
        except GestureRefused as refusal:
            reason = f"{name}: {refusal}"
            if refusal.nature == "conflict":
                raise conflict(reason) from refusal
            if refusal.nature == "reauth":
                raise unauthorized(f"{reason} — sign in again (GET /api/v1/auth/login?reauth=1)") from refusal
            raise forbidden(reason) from refusal


def reset_gesture_checks() -> None:
    for checks in _GESTURE_CHECKS.values():
        checks.clear()


# ───────────────────────────── the theme provider (S39-03, ADR 0064) ─────────────────────────────
#
# The community edition serves its default theme. An extension that customises the look declares ONE
# provider: `provider(session, org) -> dict | None` (sync or async), where `org` is the slug the
# console asked for, or None for the instance. The core validates whatever it returns against the
# contract and the console's contrast budget (`choregos_api.theme`); an invalid theme is replaced by
# the default, never served.

_THEME_PROVIDER: list[Any] = []


def declare_theme_provider(provider: Any) -> None:
    """Called by an extension when it loads. A second declaration stops the start: two extensions
    deciding the look of the same console would be a silent last-one-wins."""
    if not callable(provider):
        raise TypeError("a theme provider is a callable (session, org) -> dict | None")
    if _THEME_PROVIDER and _THEME_PROVIDER[0] is not provider:
        raise RuntimeError("a theme provider is already declared; only one is allowed")
    if not _THEME_PROVIDER:
        _THEME_PROVIDER.append(provider)


def theme_provider() -> Any | None:
    return _THEME_PROVIDER[0] if _THEME_PROVIDER else None


# ───────────────────────────── the former names (removed in 0.23.0) ─────────────────────────────

_FORMER = {
    "declarer_un_routeur": "declare_router",  # lang: fr-ok alias:external
    "routeurs_declares": "declared_routers",  # lang: fr-ok alias:external
    "reinitialiser": "reset",  # lang: fr-ok alias:external
    "declarer_une_section_d_administration": "declare_admin_section",  # lang: fr-ok alias:external
    "sections_declarees": "declared_sections",  # lang: fr-ok alias:external
    "chemins_d_une_section": "section_paths",  # lang: fr-ok alias:external
    "FournisseurDOutils": "ToolProvider",  # lang: fr-ok alias:external
    "declarer_un_fournisseur_d_outils": "declare_tool_provider",  # lang: fr-ok alias:external
    "fournisseurs_d_outils": "tool_providers",  # lang: fr-ok alias:external
    "FournisseurHumain": "HumanToolProvider",  # lang: fr-ok alias:external
    "declarer_des_outils_pour_les_humains": "declare_human_tools",  # lang: fr-ok alias:external
    "fournisseurs_humains": "human_tool_providers",  # lang: fr-ok alias:external
    "declarer_un_installateur_de_gabarit": "declare_template_installer",  # lang: fr-ok alias:external
    "installateurs_de_gabarit": "template_installers",  # lang: fr-ok alias:external
    "GESTES": "GESTURES",  # lang: fr-ok alias:external
    "DemandeDeGeste": "GestureRequest",  # lang: fr-ok alias:external
    "GesteRefuse": "GestureRefused",  # lang: fr-ok alias:external
    "declarer_un_controle_de_geste": "declare_gesture_check",  # lang: fr-ok alias:external
    "controler": "check_gesture",  # lang: fr-ok alias:external
    "reinitialiser_les_controles": "reset_gesture_checks",  # lang: fr-ok alias:external
    "_ROUTEURS": "_ROUTERS",  # lang: fr-ok alias:external
    "_FOURNISSEURS_D_OUTILS": "_TOOL_PROVIDERS",  # lang: fr-ok alias:external
    "_FOURNISSEURS_HUMAINS": "_HUMAN_TOOL_PROVIDERS",  # lang: fr-ok alias:external
    "_INSTALLATEURS": "_INSTALLERS",  # lang: fr-ok alias:external
    "_CONTROLES": "_GESTURE_CHECKS",  # lang: fr-ok alias:external
    "_televersements": "_uploads",  # lang: fr-ok alias:external
}
alias_names(__name__, _FORMER, removed_in=EXTERNAL_ALIAS_REMOVAL)
