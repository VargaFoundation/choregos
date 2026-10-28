# SPDX-License-Identifier: Apache-2.0
"""Does Choregos still only consume an MCP gateway, and never become one?

[ADR 0105](../../docs/adr/0105-choregos-consumes-an-mcp-gateway-it-never-is-one.md): Choregos consumes
the MCP gateway an organisation runs, through MCP and standard HTTP authentication only, behind the
broker. It never routes, aggregates or re-serves third-party MCP servers; the plugin servers it runs under
ADR 0096 are its own, whitelisted below. As ADR 0079 does for graal, the boundary is a test, and it has
four parts:

  * no vendor name in what ships (a vendor dependency would arrive as an SDK import, a `*_URL` setting,
    a header or a host name, not as an `import` of a gateway);
  * the inbound MCP door and the A2A door never import what reaches an organisation's connectors
    (the broker, the MCP client, the gateway services, a platform-run plugin server);
  * nothing the API serves forwards to, merges or names a third-party MCP server: no route that selects an
    upstream, no catch-all, no virtual server, no raw listener that is not a known forward proxy, no
    subclass of the MCP client that is not the platform-run plugin servers' own;
  * nothing in the orchestrator or the runner reads the gateway (the chart's half is S46-12's).

The vendor names in the samples below are on purpose. This file lives under `tests/`, which is not one of
`TREES`, so the scan never reads it; if a tree that holds it is ever added, it belongs in `ALLOWED_FILES`.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from collections.abc import Iterable
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]

#: What ships or runs: code, charts, templates, the demo, the deployment and CI files. The same trees as
#: `test_independent_products.py`.
TREES = (
    "packages",
    "apps",
    "templates",
    "charts",
    "infra",
    "examples",
    "tools",
    "demo",
    "docker",
    "dev",
    "integrations",
    ".github",
    "compose.yaml",
    "Makefile",
    "pyproject.toml",
    "package.json",
    "justfile",
)

#: Optional data: a catalogue entry or an exclusion reason may name a product without Choregos needing it.
ALLOWED_FILES = frozenset(
    {
        "packages/core/src/choregos_core/mcp_catalogue/catalogue.yaml",
        "tools/catalogues/mcp/exclusions.yaml",
    }
)

#: A gateway product's name. Not `\b`: `_` is a word character there, and `\btoolhive\b` does not match
#: `TOOLHIVE_URL`. The look-arounds refuse a letter or a digit on either side instead, so `robots.txt`
#: is spared and `TOOLHIVE_URL`, `https://obot.example` and `docker_mcp_gateway` are caught.
VENDOR = re.compile(
    r"(?<![A-Za-z0-9])"
    r"(agentgateway|toolhive|context-?forge|mcp-context-forge|obot|mcpjungle|docker[ _-]?mcp[ _-]?gateway)"
    r"(?![A-Za-z0-9])",
    re.IGNORECASE,
)

#: The inbound MCP door (ADR 0030) and the A2A door (ADR 0078).
DOOR_DIRS = ("apps/api/src/choregos_api/mcp", "apps/api/src/choregos_api/a2a")

#: What reaches an organisation's connectors: the broker, the MCP client, the gateway services. A door
#: serves Choregos's own tools and workflows; if it imported one of these it would forward a call.
FORBIDDEN_MODULES = frozenset(
    {
        "choregos_api.services.courtier",
        "choregos_api.services.broker",
        "choregos_api.services.mcp_gateway",
        "choregos_api.services.mcp_gateway_identity",
        "choregos_adapters.mcp",
        "choregos_adapters.mcp.client",
        # `build(kind, type, config)` is how an organisation connector's client is made.
        "choregos_adapters.build",
        # A platform-run plugin server is an organisation connector, and the doors must not reach it
        # either (ADR 0096).
        "choregos_api.services.plugin_servers",
        "choregos_api.services.plugin_tracker",
        "choregos_adapters.plugin_servers",
        # The routers that hold the broker's routes: a door that imported one would hold the broker too.
        "choregos_api.routers.internal",
        "choregos_api.routers.agent_session_pods",
    }
)

#: ADR 0096: the platform runs a plugin's MCP server in the cluster and governs it as an organisation `mcp`
#: connector (`org_connectors.plugin_server`). These modules install it, address it at the Service URL the
#: platform wrote, or stand in for it. It is not an upstream and never sits behind the gateway. The doors
#: must not reach any of them.
PLUGIN_SERVER_SURFACE = frozenset(
    {
        "choregos_adapters.plugin_servers",  # `PluginServerClient` subclasses the MCP client
        "choregos_adapters.tracker.plugin",
        "choregos_adapters.fakes.plugin_tracker",
        "choregos_api.services.plugin_servers",
        "choregos_api.services.plugin_tracker",
        "choregos_api.services.plugin_installer",
        "choregos_api.routers.plugins",
        "choregos_api.routers.plugin_sources",
    }
)

#: Raw TCP listeners the API ships. Both are HTTP forward proxies for traffic that is not MCP: the relay
#: puts the search engines behind an organisation's outbound proxy (ADR 0091), the egress proxy enforces a
#: run's network access (ADR 0070).
FORWARD_PROXIES = frozenset({"choregos_api.relay", "choregos_api.egress"})

#: ADR 0096 (the tracker half): a third party's delivery, handed to the plugin's own server's
#: `tracker_parse_event`. The URL names the organisation, the plugin and the server the plugin brings.
PLUGIN_WEBHOOK = "POST /webhooks/plugins/{org}/{plugin}/{server}"

#: What the checks below let through, and why each is not a gateway: modules (a package name covers what is
#: beneath it) and routes (`VERB /path`). `test_the_whitelist_names_existing_modules` resolves every entry,
#: so a rename cannot leave a stale one behind.
ALLOWED_PLATFORM_MCP = PLUGIN_SERVER_SURFACE | FORWARD_PROXIES | {PLUGIN_WEBHOOK}

#: The routes of the two doors, by directory, one `VERB /path` per verb, parameter names included: a door
#: that took a `{connector}` or a `{server}` would serve another server. A new route is a decision: one that
#: serves Choregos's own tools or workflows is added here; one that forwards to, merges or names another MCP
#: server is a gateway.
DOOR_ROUTES = {
    "apps/api/src/choregos_api/mcp": frozenset(
        {
            "GET /.well-known/oauth-protected-resource",
            "GET /.well-known/oauth-protected-resource/{path:path}",
            "GET /mcp",
            "DELETE /mcp",
            "POST /mcp",
            "GET /mcp/projects/{project}",
            "DELETE /mcp/projects/{project}",
            "POST /mcp/projects/{project}",
        }
    ),
    "apps/api/src/choregos_api/a2a": frozenset(
        {
            "POST /a2a/orgs/{org}",
            "GET /a2a/orgs/{org}/.well-known/agent-card.json",
        }
    ),
}

#: The door's parameters still have French names until the English rename of the MCP door (plan story
#: S45-14); the freeze reads them under their English names. A rename to any other name changes the freeze,
#: and is a decision like a new route.
FORMER_PARAMETER_NAMES = {"projet": "project", "chemin": "path"}  # lang: fr-ok

#: The broker's routes: an agent lists and calls its tools per run (or per hosted session), never per
#: connector or per server.
BROKER_ROUTES = frozenset(
    {
        "GET /internal/runs/{id}/tools",
        "POST /internal/runs/{id}/tools/{name}",
        "GET /internal/agent-sessions/{id}/tools",
        "POST /internal/agent-sessions/{id}/tools/{name}",
    }
)

#: The routes that address one connector (`/connectors/{...}`), as the live application serves them, the
#: API prefix removed. They configure a connector, its secrets and its operations; none of them calls it. A
#: new one is a decision: one that forwards to the connector (`/connectors/{name}/mcp`, `.../call`) is a
#: passthrough, so a new route here needs ADR 0105 amended first.
CONNECTOR_ROUTES = frozenset(
    {
        "PUT /projects/{id}/connectors/{kind}",
        "PUT /projects/{id}/connectors/{kind}/secrets/{field}",
        "DELETE /projects/{id}/connectors/{kind}/secrets/{field}",
        "POST /projects/{id}/connectors/{kind}/test",
        "GET /orgs/{org}/connectors/{name}",
        "PATCH /orgs/{org}/connectors/{name}",
        "DELETE /orgs/{org}/connectors/{name}",
        "PUT /orgs/{org}/connectors/{name}/secrets/{field}",
        "DELETE /orgs/{org}/connectors/{name}/secrets/{field}",
        "PATCH /orgs/{org}/connectors/{name}/operations/{operation}",
        "POST /orgs/{org}/connectors/{name}/discover",
    }
)

#: The internal routes (what a run's pod calls) that take a parameter naming a connector, a tool, an input
#: or a server, as the live application serves them: the broker's two calls, a run's inputs and outputs, and
#: the relay reading an organisation's outbound proxy for a kind of traffic (ADR 0091). A new one needs
#: ADR 0105 amended first: a per-connector or per-server internal route is a gateway.
INTERNAL_NAMED_ROUTES = frozenset(
    {
        *(route for route in BROKER_ROUTES if route.startswith("POST ")),
        "GET /internal/runs/{id}/inputs/{name}",
        "PUT /internal/runs/{id}/outputs/{name}",
        "GET /internal/orgs/{org}/outbound-proxy/{kind}",
    }
)

#: A path parameter that would pick a third-party server, or an upstream, from the URL.
UPSTREAM_PARAMETERS = frozenset({"server", "upstream", "mcp_server", "virtual_server", "backend"})

#: The parameters of an internal route that name what it reaches: `INTERNAL_NAMED_ROUTES` freezes them.
NAMING_PARAMETERS = UPSTREAM_PARAMETERS | {"connector", "connector_name", "name", "kind", "tool"}

#: A path segment that names what a gateway offers.
GATEWAY_SEGMENTS = re.compile(
    r"virtual[-_]?server|aggregat|passthrough|pass-through|multiplex", re.IGNORECASE
)

#: The orchestrator and the runner never learn where an organisation's gateway is.
GATEWAY_NAME = re.compile(r"mcp[ _-]?gateway", re.IGNORECASE)
NO_GATEWAY_TREES = ("apps/orchestrator/src", "packages/runner/src")

API_SOURCE = "apps/api/src/choregos_api"
#: The prefix the application mounts its routers under; the live routes are compared without it.
API_PREFIX = "/api/v1"
SOURCE_ROOTS = (*sorted(ROOT.glob("packages/*/src")), *sorted(ROOT.glob("apps/*/src")))
HTTP_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options", "api_route", "websocket"})
#: A route added by a call rather than a decorator: `add_api_route` and Starlette's `add_route` (FastAPI's
#: GET when it lists no methods), the websocket ones, and `mount` (a whole sub-application under a path, read
#: as `MOUNT /path`). The owner may be any expression: `app.router.mount(...)` is a mount too.
ROUTE_CALLS = frozenset(
    {"add_api_route", "add_route", "add_websocket_route", "add_api_websocket_route", "mount"}
)
WEBSOCKET_CALLS = frozenset({"add_websocket_route", "add_api_websocket_route"})
#: A mount hands every path beneath it to another application, whatever that application is.
MOUNT = "MOUNT"
MCP_CLIENTS = frozenset({"ClientMcp", "McpClient", "PluginServerClient"})
#: `asyncio.start_server` and the event loop's `create_server`, TCP or Unix.
LISTENERS = frozenset({"start_server", "start_unix_server", "create_server", "create_unix_server"})
#: An import the AST does not see as one: a door must not make any.
DYNAMIC_IMPORTS = frozenset({"import_module", "__import__"})
#: ADR 0096: the one class outside the client's own module that extends the MCP client at runtime.
RUNTIME_MCP_CLIENTS = frozenset({"choregos_adapters.plugin_servers.PluginServerClient"})
#: The packages the runtime check imports module by module, and the modules that already fail to import on
#: main (none): a module that cannot be imported cannot be checked, so a new failure is a failure here.
RUNTIME_PACKAGES = ("choregos_api", "choregos_adapters")
KNOWN_IMPORT_FAILURES: frozenset[str] = frozenset()
PATH_PARAMETER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::([a-z]+))?\}")


def _tracked(*trees: str) -> list[pathlib.Path]:
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--", *trees],
        cwd=ROOT,
        check=True,
        capture_output=True,
    ).stdout.decode()
    return [ROOT / name for name in listed.split("\0") if name]


def _tracked_files() -> list[pathlib.Path]:
    return [path for path in _tracked(*TREES) if path.relative_to(ROOT).as_posix() not in ALLOWED_FILES]


def _python_files(*directories: str) -> list[pathlib.Path]:
    return sorted(
        path for directory in directories for path in (ROOT / directory).rglob("*.py") if path.is_file()
    )


# --- the vendor names -------------------------------------------------------------------------------


def test_there_is_code_to_check() -> None:
    """Without this guard, a moved tree would make the suite green and empty."""
    files = _tracked_files()
    assert len(files) > 1000, f"only {len(files)} files found: the trees have moved"


def test_the_pattern_catches_vendor_code_and_spares_ordinary_words() -> None:
    caught = (
        "from agentgateway import Client",
        "TOOLHIVE_URL",  # `\btoolhive\b` does not match it: `_` is a word character
        "AGENTGATEWAY_TOKEN",
        "contextforge-admin",
        "https://obot.example/api",
        "Docker MCP Gateway",
        "docker_mcp_gateway",
    )
    spared = (
        "robots.txt",
        "an MCP gateway",
        "mcp_gateway",
        "McpGateway",
        "FakeMcpGateway",
        "LiteLlmGateway",
        "Kong AI Gateway",
    )
    assert all(VENDOR.search(text) for text in caught), "the pattern no longer sees a gateway product"
    assert not any(VENDOR.search(text) for text in spared), "the pattern flags an ordinary word"


def test_nothing_that_ships_names_an_mcp_gateway_product() -> None:
    offenders: list[str] = []
    for path in _tracked_files():
        if not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if VENDOR.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:120]}")
    assert not offenders, (
        "Choregos names an MCP gateway product outside the catalogue files:\n  "
        + "\n  ".join(offenders)
        + "\nA gateway is met through MCP Streamable HTTP and standard HTTP authentication only "
        "(ADR 0105); a product's name belongs in docs/, as an example."
    )


# --- the doors' imports -----------------------------------------------------------------------------


def _resolve(module: str, is_package: bool, level: int, name: str | None) -> str | None:
    """The absolute name `from <dots><name> import ...` means in `module`; None if it climbs too far."""
    if level == 0:
        return name
    parts = module.split(".")
    keep = len(parts) - level + (1 if is_package else 0)
    if keep < 1:
        return None
    return ".".join([*parts[:keep], name] if name else parts[:keep])


def _is_forbidden(name: str, forbidden: Iterable[str]) -> bool:
    return any(name == module or name.startswith(f"{module}.") for module in forbidden)


def forbidden_imports(
    source: str, module: str, is_package: bool = False, *, forbidden: Iterable[str] = FORBIDDEN_MODULES
) -> list[str]:
    """The forbidden modules `source` (the code of `module`) imports, relative imports resolved.

    An `ImportFrom` names a module AND names: `from ..services import broker` is the module
    `choregos_api.services`, and also `choregos_api.services.broker`. Both are tested, and a prefix counts
    (`choregos_adapters.mcp.x` is beneath `choregos_adapters.mcp`).
    """
    forbidden = frozenset(forbidden)
    found: list[str] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve(module, is_package, node.level, node.module)
            if resolved is None:
                continue
            names = [resolved]
            if not _is_forbidden(resolved, forbidden):
                names = [f"{resolved}.{alias.name}" for alias in node.names if alias.name != "*"]
        else:
            continue
        found += [name for name in names if _is_forbidden(name, forbidden)]
    return list(dict.fromkeys(found))


def forbidden_attributes(
    source: str, module: str, is_package: bool = False, *, forbidden: Iterable[str] = FORBIDDEN_MODULES
) -> list[str]:
    """The forbidden modules `source` reaches by attribute from a package it imports.

    `from .. import services as s` then `s.broker.call(...)` imports no forbidden module, and reaches one
    whenever something else has imported it. Each imported name is followed through its attributes.
    """
    forbidden = frozenset(forbidden)
    tree = ast.parse(source)
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    bound[alias.asname] = alias.name
                else:
                    top = alias.name.split(".")[0]
                    bound[top] = top
        elif isinstance(node, ast.ImportFrom):
            resolved = _resolve(module, is_package, node.level, node.module)
            if resolved is not None:
                for alias in node.names:
                    if alias.name != "*":
                        bound[alias.asname or alias.name] = f"{resolved}.{alias.name}"
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain: list[str] = []
        current: ast.expr = node
        while isinstance(current, ast.Attribute):
            chain.insert(0, current.attr)
            current = current.value
        if isinstance(current, ast.Name) and current.id in bound:
            name = ".".join([bound[current.id], *chain])
            if _is_forbidden(name, forbidden):
                reached = [module for module in forbidden if _is_forbidden(name, [module])]
                found.append(max(reached, key=len))
    return list(dict.fromkeys(found))


def _module_of(path: pathlib.Path) -> tuple[str, bool]:
    """(dotted name, is it an `__init__.py`) of a file under a package's or an application's `src`."""
    for root in SOURCE_ROOTS:
        if root in path.parents:
            parts = list(path.relative_to(root).with_suffix("").parts)
            if parts[-1] == "__init__":
                return ".".join(parts[:-1]), True
            return ".".join(parts), False
    raise ValueError(f"{path} is under no source root")


def test_the_import_check_sees_through_relative_and_from_imports() -> None:
    door = "choregos_api.mcp.outils"
    assert forbidden_imports("from ..services import broker", door) == ["choregos_api.services.broker"]
    assert forbidden_imports("from ..services import courtier", door) == ["choregos_api.services.courtier"]
    assert forbidden_imports("from ..services.courtier import outils_du_courtier", door) == [
        "choregos_api.services.courtier"
    ]
    assert forbidden_imports("from choregos_adapters import build", door) == ["choregos_adapters.build"]
    assert forbidden_imports("import choregos_adapters.mcp.client", door) == ["choregos_adapters.mcp.client"]
    assert forbidden_imports("from choregos_adapters.mcp.x import y", door) == ["choregos_adapters.mcp.x"]
    assert forbidden_imports("from choregos_adapters.mcp import ClientMcp", door) == ["choregos_adapters.mcp"]
    # A sibling import, and the same line in an `__init__.py` (one level less to climb).
    assert forbidden_imports("from . import courtier", "choregos_api.services.x") == [
        "choregos_api.services.courtier"
    ]
    assert forbidden_imports("from ..services import broker", "choregos_api.mcp", is_package=True) == [
        "choregos_api.services.broker"
    ]
    assert forbidden_imports("from ..services import broker", "choregos_api.mcp") == []  # climbs too far
    assert forbidden_imports("from .courtier import outils_du_courtier", "choregos_api.services", True) == [
        "choregos_api.services.courtier"
    ]
    # What a door does import.
    assert forbidden_imports("from ..services import creer_un_ticket", door) == []
    assert forbidden_imports("from choregos_adapters.storage import ObjectStoreError", door) == []
    assert forbidden_imports("import choregos_adapters", door) == []
    assert forbidden_imports("from choregos_adapters.mcpx import y", door) == []  # a prefix ends at a dot
    # The plugin surface is checked with its own list.
    plugin = "from ..routers import plugins"
    assert forbidden_imports(plugin, door) == []
    assert forbidden_imports(plugin, door, forbidden=PLUGIN_SERVER_SURFACE) == [
        "choregos_api.routers.plugins"
    ]


def test_the_mcp_and_a2a_doors_never_reach_an_organisation_connector() -> None:
    files = _python_files(*DOOR_DIRS)
    assert len(files) >= 6, f"only {len(files)} door files found: the doors have moved"
    reached: dict[str, list[str]] = {}
    for path in files:
        module, is_package = _module_of(path)
        source = path.read_text("utf-8")
        forbidden = FORBIDDEN_MODULES | PLUGIN_SERVER_SURFACE
        found = forbidden_imports(source, module, is_package, forbidden=forbidden)
        found += forbidden_attributes(source, module, is_package, forbidden=forbidden)
        found += [f"a dynamic import, line {line}" for line in dynamic_imports(source)]
        if found:
            reached[path.relative_to(ROOT).as_posix()] = list(dict.fromkeys(found))
    assert not reached, (
        f"a door imports what reaches an organisation's connectors: {reached}. The MCP and A2A doors serve "
        "Choregos's own tools and workflows; forwarding a call to another MCP server is what a gateway does, "
        "and Choregos consumes one, it never is one (ADR 0105)."
    )


def test_the_services_package_re_exports_nothing_that_reaches_an_organisation_connector() -> None:
    """The doors import names from `..services`: a re-export there would carry the broker to them."""
    path = ROOT / API_SOURCE / "services" / "__init__.py"
    module, is_package = _module_of(path)
    assert (module, is_package) == ("choregos_api.services", True)
    found = forbidden_imports(
        path.read_text("utf-8"), module, is_package, forbidden=FORBIDDEN_MODULES | PLUGIN_SERVER_SURFACE
    )
    assert not found, (
        f"`choregos_api.services` re-exports {found}: the doors import from it by name, so they would "
        "reach an organisation's connectors without importing the broker (ADR 0105)."
    )


# --- the routes, the client, the listeners ----------------------------------------------------------


def _name_of(node: ast.expr) -> str:
    """`x` for `x` and for `a.b.x`: the last name an expression spells, or an empty string."""
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else ""


def _route_path(call: ast.Call) -> str | None:
    """The path a route decorator or call names: its `path=` or first positional argument.

    A literal is read as written. An f-string is read with each interpolated name as a parameter of that name
    (`f"{API_PREFIX}/edition"` is `{API_PREFIX}/edition`) and any other expression as `{}`, so a gateway
    route cannot hide behind one. Anything else (a variable) is not read.
    """
    candidates = [argument.value for argument in call.keywords if argument.arg == "path"] + call.args[:1]
    for candidate in candidates:
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str):
            return candidate.value
        if isinstance(candidate, ast.JoinedStr):
            return "".join(
                str(part.value)
                if isinstance(part, ast.Constant)
                else f"{{{_name_of(part.value) if isinstance(part, ast.FormattedValue) else ''}}}"
                for part in candidate.values
            )
    return None


def _verbs(call: ast.Call, default: str) -> str:
    """The `methods=[...]` a route decorator or call lists, comma-separated, or `default` if it lists none."""
    for argument in call.keywords:
        if argument.arg == "methods" and isinstance(argument.value, ast.List | ast.Tuple | ast.Set):
            return ",".join(
                str(item.value).upper() for item in argument.value.elts if isinstance(item, ast.Constant)
            )
    return default


def _call_routes(tree: ast.AST, prefixes: dict[str, str]) -> list[str]:
    """The routes `tree` adds by a call (`ROUTE_CALLS`), on any owner: `app.router.mount(...)` too."""
    routes: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        owner, attr = node.func.value, node.func.attr
        path = _route_path(node) if attr in ROUTE_CALLS else None
        if path is None:
            continue
        verbs = MOUNT if attr == "mount" else _verbs(node, "WEBSOCKET" if attr in WEBSOCKET_CALLS else "GET")
        prefix = prefixes.get(owner.id, "") if isinstance(owner, ast.Name) else ""
        routes.append(f"{verbs} {prefix}{path}")
    return routes


def routes_of(source: str) -> list[str]:
    """`VERB /path` of every FastAPI route in `source`, the router's own prefix joined.

    A route is a decorator (`@router.post(...)`, `@router.api_route(..., methods=[...])`) or a call:
    `add_api_route(path, ..., methods=[...])` and `mount(path, ...)`, read as `MOUNT /path`.
    """
    tree = ast.parse(source)
    prefixes: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Call)
            and _name_of(node.value.func) == "APIRouter"
        ):
            prefix = ""
            for argument in node.value.keywords:
                if argument.arg == "prefix" and isinstance(argument.value, ast.Constant):
                    prefix = str(argument.value.value)
            prefixes |= {target.id: prefix for target in node.targets if isinstance(target, ast.Name)}
    routes: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        for decorator in node.decorator_list:
            if not (isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute)):
                continue
            router = decorator.func.value
            if decorator.func.attr not in HTTP_VERBS or not isinstance(router, ast.Name):
                continue
            path = _route_path(decorator)
            if path is None:
                continue
            attr = decorator.func.attr
            verbs = _verbs(decorator, "GET" if attr == "api_route" else attr.upper())
            routes.append(f"{verbs} {prefixes.get(router.id, '')}{path}")
    routes += _call_routes(tree, prefixes)
    return routes


def _english_parameter(match: re.Match[str]) -> str:
    name = FORMER_PARAMETER_NAMES.get(match[1], match[1])
    return f"{{{name}:{match[2]}}}" if match[2] else f"{{{name}}}"


def frozen_routes(routes: Iterable[str]) -> frozenset[str]:
    """What the freeze compares: one `VERB /path` per verb, parameter names included.

    A parameter's name says what a route selects (a project, an organisation, a run, or a connector, a
    server), so it is part of the route. Only the door's former French names are read as their English ones.
    """
    found: set[str] = set()
    for route in routes:
        verbs, path = route.split(" ", 1)
        english = PATH_PARAMETER.sub(_english_parameter, path)
        found |= {f"{verb} {english}" for verb in verbs.split(",")}
    return frozenset(found)


def gateway_traits(route: str) -> list[str]:
    """What in a route's path says it forwards to, merges or names a third-party server."""
    verbs, path = route.split(" ", 1)
    traits: list[str] = []
    if verbs == MOUNT:
        traits.append("a mount hands every sub-path to another application")
    for name, converter in PATH_PARAMETER.findall(path):
        if name in UPSTREAM_PARAMETERS:
            traits.append(f"`{{{name}}}` picks an upstream")
        if converter == "path" and not path.startswith("/.well-known/"):
            traits.append(f"`{{{name}:path}}` catches every sub-path")
    if GATEWAY_SEGMENTS.search(path):
        traits.append("a segment names what a gateway offers")
    return traits


def aliases(tree: ast.AST, names: frozenset[str]) -> frozenset[str]:
    """`names` and every local name `tree` binds to one of them.

    `from asyncio import start_server as listen` binds `listen`; `listen = asyncio.start_server` and
    `Upstream = McpClient` too, and an alias of an alias, so a renamed import cannot hide a call or a base.
    """
    known = set(names)
    while True:
        found = set(known)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                found |= {alias.asname for alias in node.names if alias.name in known and alias.asname}
            elif isinstance(node, ast.Assign | ast.AnnAssign) and isinstance(
                node.value, ast.Name | ast.Attribute
            ):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if _name_of(node.value) in known:
                    found |= {target.id for target in targets if isinstance(target, ast.Name)}
        if found == known:
            return frozenset(known)
        known = found


def mcp_client_subclasses(source: str) -> list[str]:
    """The classes of `source` that extend an MCP client, under its own name or an alias."""
    tree = ast.parse(source)
    clients = aliases(tree, MCP_CLIENTS)
    return [
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and any(_name_of(base) in clients for base in node.bases)
    ]


def _calls(source: str, names: frozenset[str]) -> list[int]:
    """The lines of `source` that call one of `names`, under its own name or an alias."""
    tree = ast.parse(source)
    called = aliases(tree, names)
    return [
        node.lineno for node in ast.walk(tree) if isinstance(node, ast.Call) and _name_of(node.func) in called
    ]


def listeners(source: str) -> list[int]:
    """The lines of `source` that open a raw TCP or Unix listener."""
    return _calls(source, LISTENERS)


def dynamic_imports(source: str) -> list[int]:
    """The lines of `source` that import a module by a name computed at runtime."""
    return _calls(source, DYNAMIC_IMPORTS)


@functools.cache
def _api_routes() -> dict[str, list[str]]:
    return {
        path.relative_to(ROOT).as_posix(): routes_of(path.read_text("utf-8"))
        for path in _python_files(API_SOURCE)
    }


def _allowed(module: str) -> bool:
    return _is_forbidden(module, (entry for entry in ALLOWED_PLATFORM_MCP if " " not in entry))


def test_the_route_check_sees_a_gateway_and_spares_the_doors() -> None:
    source = """
router = APIRouter(prefix="/gw")
other = APIRouter()

@router.post("/{server}/mcp")
async def passthrough(): ...

@router.api_route("/mcp/{upstream}", methods=["GET", "POST"])
async def upstream(): ...

@other.get("/virtual-servers")
async def virtual(): ...

@other.get("/orgs/{org}/aggregated-tools")
async def merged(): ...

@other.get("/files/{rest:path}")
async def catch_all(): ...

@other.get("/.well-known/thing/{rest:path}")
async def metadata(): ...

@other.get("/runs/{id}/tools/{name}")
async def tool(): ...

@other.get("/orgs/{org}/mcp-gateway")
async def setting(): ...

def helper(): ...

def create_app():
    router.add_api_route("/{upstream}/sse", forward, methods=["POST"])
    app.add_api_route(path="/orgs/{org}/edition", endpoint=edition)
    app.mount("/servers/{server}", proxy)
    app.mount("/static", files)
    app.add_api_route(f"{API_PREFIX}/edition", edition)
    router.add_api_route(f"/{settings.upstream_prefix}/{{rest:path}}", forward)
    app.add_api_route(PATH, forward)
    app.router.mount("/mcp-servers", proxy)
    app.add_route("/servers/{server}/mcp", forward, methods=["POST"])
    app.add_websocket_route("/ws/{upstream}", forward)
"""
    routes = routes_of(source)
    assert routes == [
        "POST /gw/{server}/mcp",
        "GET,POST /gw/mcp/{upstream}",
        "GET /virtual-servers",
        "GET /orgs/{org}/aggregated-tools",
        "GET /files/{rest:path}",
        "GET /.well-known/thing/{rest:path}",
        "GET /runs/{id}/tools/{name}",
        "GET /orgs/{org}/mcp-gateway",
        # `add_api_route` with its router's prefix, then FastAPI's GET when it lists no methods.
        "POST /gw/{upstream}/sse",
        "GET /orgs/{org}/edition",
        "MOUNT /servers/{server}",
        "MOUNT /static",
        # An f-string path: an interpolated name is a parameter of that name, `{{`/`}}` a literal brace pair.
        "GET {API_PREFIX}/edition",
        "GET /gw/{upstream_prefix}/{rest:path}",
        # `app.add_api_route(PATH, ...)`: a variable path is not read (the live routes are, below).
        # Any owner: `app.router.mount`; Starlette's `add_route` and the websocket routes.
        "MOUNT /mcp-servers",
        "POST /servers/{server}/mcp",
        "WEBSOCKET /ws/{upstream}",
    ]
    flagged = [route for route in routes if gateway_traits(route)]
    # Every mount is flagged, `/static` too: a mount hands every sub-path to another application.
    assert flagged == [*routes[:5], routes[8], routes[10], routes[11], routes[13], *routes[14:]]
    assert gateway_traits("MOUNT /static") == ["a mount hands every sub-path to another application"]
    # The platform's own plugin webhook is the one route of the API that names a server.
    assert gateway_traits(PLUGIN_WEBHOOK) == ["`{server}` picks an upstream"]


def test_the_route_freeze_compares_names_and_verbs() -> None:
    """A parameter's name is part of a route, as are a segment, a converter, a verb and a mount."""
    door = frozen_routes(["POST /mcp/projects/{project}", "GET,DELETE /mcp", "GET /meta/{path:path}"])
    assert door == {"POST /mcp/projects/{project}", "GET /mcp", "DELETE /mcp", "GET /meta/{path:path}"}
    # The order of the verbs and the door's former French names do not matter.
    assert frozen_routes(["POST /mcp/projects/{project}", "DELETE,GET /mcp", "GET /meta/{path:path}"]) == door
    former = ["POST /mcp/projects/{projet}", "GET,DELETE /mcp", "GET /meta/{chemin:path}"]  # lang: fr-ok
    assert frozen_routes(former) == door
    for changed in (
        ["POST /mcp/projects/{connector}", "GET,DELETE /mcp", "GET /meta/{path:path}"],  # another name
        ["POST /mcp/projects/{project_id}", "GET,DELETE /mcp", "GET /meta/{path:path}"],  # another name
        ["POST /mcp/projects/{project}/servers", "GET,DELETE /mcp", "GET /meta/{path:path}"],  # a segment
        ["POST /mcp/projects/{project:path}", "GET,DELETE /mcp", "GET /meta/{path:path}"],  # a converter
        ["POST /mcp/projects/{project}", "GET,DELETE,POST /mcp", "GET /meta/{path:path}"],  # a verb
        ["POST /mcp/projects/{project}", "GET,DELETE /mcp", "MOUNT /meta/{path:path}"],  # a mount
        ["POST /mcp/projects/{project}", "GET,DELETE /mcp"],  # a removed route
    ):
        assert frozen_routes(changed) != door, changed
    # The broker's names are English: `{name}` turned into `{connector}` is a change.
    assert frozen_routes(["POST /internal/runs/{id}/tools/{connector}"]) != frozen_routes(
        ["POST /internal/runs/{id}/tools/{name}"]
    )


def test_the_client_and_listener_checks_see_what_they_guard() -> None:
    assert mcp_client_subclasses("class Mine(ClientMcp): ...") == ["Mine"]
    assert mcp_client_subclasses("class Mine(mcp.McpClient): ...") == ["Mine"]
    assert mcp_client_subclasses("class Mine(PluginServerClient, Base): ...") == ["Mine"]
    assert mcp_client_subclasses("class Mine(Base): ...\nclient = ClientMcp('x')") == []
    assert listeners("server = await asyncio.start_server(handler, host, port)") == [1]
    assert listeners("await start_unix_server(handler, path)") == [1]
    assert listeners("uvicorn.run(app)\nserver.start()") == []
    # The event loop's listeners, and an aliased import or assignment.
    assert listeners("server = await loop.create_server(protocol, host, port)") == [1]
    assert listeners("await asyncio.get_running_loop().create_unix_server(protocol, path)") == [1]
    assert listeners("from asyncio import start_server as listen\nawait listen(handler, host, port)") == [2]
    assert listeners("listen = asyncio.start_server\nopen_ = listen\nawait open_(handler)") == [3]
    assert mcp_client_subclasses("from choregos_adapters.mcp import McpClient as Up\nclass R(Up): ...") == [
        "R"
    ]
    assert mcp_client_subclasses("Base = mcp.ClientMcp\nclass R(Base): ...") == ["R"]
    # A door's imports by a computed name.
    assert dynamic_imports("import importlib\nm = importlib.import_module('x')") == [2]
    assert dynamic_imports("from importlib import import_module as load\nload('x')") == [2]
    assert dynamic_imports("m = __import__('choregos_api.services.broker', fromlist=['call'])") == [1]
    assert dynamic_imports("import importlib.metadata\nimportlib.metadata.version('x')") == []


def test_the_attribute_check_follows_an_imported_package() -> None:
    door = "choregos_api.mcp.outils"
    reach = "from .. import services as s\ns.broker.call()"
    assert forbidden_attributes(reach, door) == ["choregos_api.services.broker"]
    assert forbidden_attributes("import choregos_api\nchoregos_api.services.courtier.x", door) == [
        "choregos_api.services.courtier"
    ]
    assert forbidden_attributes("import choregos_adapters as a\na.mcp.McpClient('u')", door) == [
        "choregos_adapters.mcp"
    ]
    assert forbidden_attributes("from .. import routers\nrouters.internal.router", door) == [
        "choregos_api.routers.internal"
    ]
    assert forbidden_attributes("from .. import services as s\ns.parcours(x)", door) == []
    assert forbidden_attributes("import choregos_adapters\nchoregos_adapters.mcpx", door) == []


def test_no_route_selects_a_third_party_mcp_server() -> None:
    routes = _api_routes()
    assert sum(map(len, routes.values())) > 200, "the route scan found almost nothing: the API has moved"
    offenders = [
        f"{file}: {route} ({', '.join(gateway_traits(route))})"
        for file, found in routes.items()
        for route in found
        if gateway_traits(route) and route not in ALLOWED_PLATFORM_MCP
    ]
    assert not offenders, (
        "a route of the API picks, catches or merges third-party MCP servers:\n  "
        + "\n  ".join(offenders)
        + "\nA passthrough, an aggregation endpoint, a virtual server or a routing of third-party servers "
        "is a gateway, and Choregos never is one (ADR 0105). A platform-run plugin server is the one "
        "exception (ADR 0096): add it to ALLOWED_PLATFORM_MCP with the ADR that allows it."
    )


def test_the_doors_and_the_broker_keep_their_routes() -> None:
    routes = _api_routes()
    for directory, door in DOOR_ROUTES.items():
        expected = frozen_routes(door)
        found = frozen_routes(
            route for file, listed in routes.items() if file.startswith(f"{directory}/") for route in listed
        )
        assert found == expected, (
            f"the routes of {directory} changed: {sorted(found ^ expected)}. A route that serves "
            "Choregos's own tools or workflows goes into DOOR_ROUTES; one that forwards to, merges or "
            "names another MCP server is a gateway (ADR 0105)."
        )
    tools = frozen_routes(
        route for listed in routes.values() for route in listed if "/tools" in route and "/internal/" in route
    )
    broker = frozen_routes(BROKER_ROUTES)
    assert tools == broker, (
        f"the broker's routes changed: {sorted(tools ^ broker)}. An agent lists and calls tools per "
        "run or per hosted session, never per connector or per server (ADR 0105)."
    )


def test_only_the_platform_run_servers_extend_the_mcp_client() -> None:
    offenders: list[str] = []
    extending: set[str] = set()
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            module, _ = _module_of(path)
            subclasses = mcp_client_subclasses(path.read_text("utf-8"))
            if subclasses:
                extending.add(module)
            if subclasses and not (_allowed(module) or _is_forbidden(module, ["choregos_adapters.mcp"])):
                offenders.append(f"{module}: {', '.join(subclasses)}")
    assert "choregos_adapters.plugin_servers" in extending, "the check no longer sees PluginServerClient"
    assert not offenders, (
        "a class extends the MCP client outside the client's own package and the platform-run plugin "
        f"servers (ADR 0096): {offenders}. A new way to reach MCP servers is one more thing the gateway "
        "is for (ADR 0105)."
    )


def test_only_the_forward_proxies_open_a_raw_listener() -> None:
    offenders: list[str] = []
    listening: set[str] = set()
    for path in _python_files(API_SOURCE):
        module, _ = _module_of(path)
        if listeners(path.read_text("utf-8")):
            listening.add(module)
            if not _allowed(module):
                offenders.append(module)
    assert any(module.startswith("choregos_api.relay") for module in listening), "the check lost the relay"
    assert not offenders, (
        f"the API opens a raw listener outside its forward proxies: {offenders}. Something that accepts "
        "connections and forwards them is a passthrough (ADR 0105); the relay (ADR 0091) and the egress "
        "proxy (ADR 0070) are the ones the API ships."
    )


def test_the_whitelist_names_existing_modules() -> None:
    routes = {route for listed in _api_routes().values() for route in listed}
    stale: list[str] = []
    for entry in sorted(ALLOWED_PLATFORM_MCP):
        if " " in entry:
            exists = entry in routes
        else:
            relative = pathlib.Path(*entry.split("."))
            exists = any(
                (root / relative).is_dir() or (root / relative).with_suffix(".py").is_file()
                for root in SOURCE_ROOTS
            )
        if not exists:
            stale.append(entry)
    assert not stale, f"ALLOWED_PLATFORM_MCP names what no longer exists: {stale}. Rename the entry too."


# --- what runs: the doors' import closure, the live routes, the client's subclasses -----------------
#
# The checks above read the source, and the source can be written so they do not see it: a path in a
# variable, a prefix given at `include_router`, a re-export through a module the doors already import, an
# aliased base class. What runs cannot be written around, so the same rules are applied to it too: one
# subprocess (so that neither the environment nor the imports leak into the other tests) imports the doors
# alone and lists what they pulled in, builds the application and lists its routes, then imports every module
# and lists the MCP client's subclasses.

_PROBE = """
import importlib, json, os, pkgutil, sys

doors, packages, out = json.loads(sys.argv[1]), json.loads(sys.argv[2]), sys.argv[3]
for door in doors:
    importlib.import_module(door)
closure = sorted(sys.modules)

from fastapi.routing import iter_route_contexts
from starlette.routing import Mount, WebSocketRoute
from choregos_api.main import create_app

routes = []
for context in iter_route_contexts(create_app().router.routes):
    route = context.original_route
    if isinstance(route, Mount):
        verbs = ["MOUNT"]
    elif isinstance(route, WebSocketRoute):
        verbs = ["WEBSOCKET"]
    else:
        verbs = sorted(context.methods or [])
    endpoint = context.endpoint
    routes.append([verbs, context.path or "", getattr(endpoint, "__module__", "") or ""])

failures = {}
for name in packages:
    package = importlib.import_module(name)
    for found in pkgutil.walk_packages(package.__path__, name + "."):
        try:
            importlib.import_module(found.name)
        except BaseException as error:
            failures[found.name] = repr(error)[:300]

from choregos_adapters import mcp

def below(cls):
    for sub in cls.__subclasses__():
        yield sub
        yield from below(sub)

bases = (mcp.McpClient, mcp.ClientMcp)
classes = sorted({f"{c.__module__}.{c.__qualname__}" for base in bases for c in below(base)})
with open(out, "w", encoding="utf-8") as file:
    json.dump({"closure": closure, "routes": routes, "failures": failures, "subclasses": classes}, file)
"""


@functools.cache
def _probe() -> dict[str, Any]:
    doors = [_module_of(path)[0] for path in _python_files(*DOOR_DIRS)]
    with tempfile.TemporaryDirectory() as directory:
        out = pathlib.Path(directory) / "probe.json"
        environment = {**os.environ, "CHOREGOS_FAKES": "1"}
        done = subprocess.run(
            [sys.executable, "-c", _PROBE, json.dumps(doors), json.dumps(RUNTIME_PACKAGES), str(out)],
            cwd=ROOT,
            env=environment,
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        assert done.returncode == 0, f"the probe failed:\n{done.stderr[-4000:]}"
        result: dict[str, Any] = json.loads(out.read_text("utf-8"))
        return result


def _live_routes() -> list[tuple[str, str]]:
    """`(VERB /path, endpoint module)` of every route the application serves, the API prefix removed."""
    found: list[tuple[str, str]] = []
    for verbs, path, module in _probe()["routes"]:
        served = path.removeprefix(API_PREFIX) if path.startswith(f"{API_PREFIX}/") else path
        found += [(f"{verb} {served}", module) for verb in verbs]
    return found


def test_the_doors_import_nothing_that_reaches_an_organisation_connector() -> None:
    """The doors' whole import closure, not their own lines: a re-export anywhere on the way is seen."""
    closure = _probe()["closure"]
    assert "choregos_api.mcp.transport" in closure, "the probe no longer imports the doors"
    forbidden = FORBIDDEN_MODULES | PLUGIN_SERVER_SURFACE
    reached = [module for module in closure if _is_forbidden(module, forbidden)]
    assert not reached, (
        f"importing the MCP and A2A doors imports {reached}: something they import, directly or not, "
        "reaches an organisation's connectors. The doors serve Choregos's own tools and workflows; "
        "forwarding a call to another MCP server is what a gateway does (ADR 0105)."
    )


def test_no_live_route_selects_a_third_party_mcp_server() -> None:
    routes = _live_routes()
    assert len(routes) > 200, "the application serves almost nothing: the probe has moved"
    offenders = sorted(
        f"{route} ({', '.join(gateway_traits(route))}) from {module}"
        for route, module in routes
        if gateway_traits(route) and route not in ALLOWED_PLATFORM_MCP
    )
    assert not offenders, (
        "the application serves a route that picks, catches or merges third-party MCP servers:\n  "
        + "\n  ".join(offenders)
        + "\nA passthrough, an aggregation endpoint, a virtual server or a routing of third-party servers "
        "is a gateway, and Choregos never is one (ADR 0105)."
    )


def test_the_live_doors_broker_and_connector_routes_are_frozen() -> None:
    routes = _live_routes()
    for directory, door in DOOR_ROUTES.items():
        package = _module_of(ROOT / directory / "__init__.py")[0]
        found = frozen_routes(route for route, module in routes if _is_forbidden(module, [package]))
        expected = frozen_routes(door)
        assert found == expected, (
            f"the application serves other routes from {package}: {sorted(found ^ expected)}. A route that "
            "serves Choregos's own tools or workflows goes into DOOR_ROUTES; one that forwards to, merges "
            "or names another MCP server is a gateway (ADR 0105)."
        )
    paths = [route for route, _ in routes]
    tools = frozen_routes(route for route in paths if "/tools" in route and "/internal/" in route)
    assert tools == frozen_routes(BROKER_ROUTES), (
        f"the broker's routes changed: {sorted(tools ^ frozen_routes(BROKER_ROUTES))}. An agent lists and "
        "calls tools per run or per hosted session, never per connector or per server (ADR 0105)."
    )
    connectors = frozen_routes(route for route in paths if re.search(r"/connectors/\{[^}]*\}", route))
    changed = sorted(connectors ^ frozen_routes(CONNECTOR_ROUTES))
    assert not changed, (
        f"the routes that address one connector changed: {changed}."
        " They configure a connector and never call it; a route that forwards to one is a passthrough. A new "
        "one needs ADR 0105 amended before it goes into CONNECTOR_ROUTES."
    )
    named = frozen_routes(
        route
        for route in paths
        if "/internal/" in route
        and any(name in NAMING_PARAMETERS for name, _ in PATH_PARAMETER.findall(route.split(" ", 1)[1]))
    )
    assert named == frozen_routes(INTERNAL_NAMED_ROUTES), (
        f"the internal routes that take a connector, a tool or a server changed: "
        f"{sorted(named ^ frozen_routes(INTERNAL_NAMED_ROUTES))}. A per-connector or per-server internal "
        "route is a gateway; a new one needs ADR 0105 amended before it goes into INTERNAL_NAMED_ROUTES."
    )


def test_every_module_imports_and_only_the_platform_run_servers_extend_the_mcp_client() -> None:
    """The classes that exist, whatever name their base was imported under."""
    probe = _probe()
    failures = {name: error for name, error in probe["failures"].items() if name not in KNOWN_IMPORT_FAILURES}
    assert not failures, (
        f"modules of {RUNTIME_PACKAGES} no longer import, so the runtime check cannot see them: {failures}"
    )
    subclasses = set(probe["subclasses"])
    assert subclasses >= RUNTIME_MCP_CLIENTS, "the runtime check no longer sees PluginServerClient"
    offenders = sorted(subclasses - RUNTIME_MCP_CLIENTS)
    assert not offenders, (
        f"classes extend the MCP client at runtime outside the platform-run plugin servers (ADR 0096): "
        f"{offenders}. A new way to reach MCP servers is one more thing the gateway is for (ADR 0105)."
    )


# --- the orchestrator and the runner ----------------------------------------------------------------


def test_nothing_in_the_orchestrator_or_the_runner_reads_the_gateway() -> None:
    assert all(
        GATEWAY_NAME.search(text) for text in ("mcp_gateway", "CHOREGOS_MCP_GATEWAY_URL", "MCP gateway")
    )
    assert not any(GATEWAY_NAME.search(text) for text in ("gateway_url", "LiteLLM gateway", "mcp_server"))
    files = _tracked(*NO_GATEWAY_TREES)
    assert len(files) > 50, f"only {len(files)} files found: the trees have moved"
    offenders: list[str] = []
    for path in files:
        text = path.read_bytes().decode("utf-8", errors="ignore")
        for number, line in enumerate(text.splitlines(), start=1):
            if GATEWAY_NAME.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}: {line.strip()[:120]}")
    assert not offenders, (
        "the orchestrator or the runner reads the MCP gateway:\n  "
        + "\n  ".join(offenders)
        + "\nOnly the API's broker and its workers' effects reach the gateway, and never a run (ADR 0105): "
        "an agent calls the broker, which calls the gateway."
    )


# --- the catalogue ----------------------------------------------------------------------------------


def test_the_catalogue_still_excludes_gateways_and_aggregators() -> None:
    document = yaml.safe_load((ROOT / "tools/catalogues/mcp/exclusions.yaml").read_text("utf-8"))
    reasons = {rule["pattern"]: rule["reason"] for rule in document["names"]}
    for pattern in ("gateway", "aggregator"):
        assert pattern in reasons, (
            f"the catalogue no longer excludes `{pattern}`: a gateway is consumed, never listed"
        )
        assert "ADR 0105" in reasons[pattern], f"the reason for `{pattern}` no longer cites ADR 0105"
