"""S47-17: every path of the API contract has a client, or a stated reason not to.

The console (`apps/web/src`, without its demo fixtures under `src/mocks`, which answer calls and
make none) and the CLI (`packages/cli/src`) are the platform's clients. A path neither of them
names is a feature nobody reaches without `curl`: the cross-backend review metric and the memory
re-import were such features until this story gave them a screen. The guard reads the contract's
paths and fails for one that no client names, unless one of the allowlists below holds it with its
reason. An entry that is no longer true (the path left the contract, or a client calls it now; a
machine-only route served outside the contract that the API no longer declares) fails too: the
allowlists say what is client-less today, not what was.

A PR that adds a path to the contract adds its client, or its entry here, in the same PR.
"""

from __future__ import annotations

import functools
import pathlib
import re
from collections.abc import Iterable

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
OPENAPI = ROOT / "packages" / "contracts" / "openapi.yaml"
WEB_SRC = ROOT / "apps" / "web" / "src"
WEB_MOCKS = WEB_SRC / "mocks"
CLI_SRC = ROOT / "packages" / "cli" / "src"
API_SRC = ROOT / "apps" / "api" / "src"

#: Routes only machines call, never a screen or a command. An entry ending in `/*` covers every path
#: under that prefix. The MCP and A2A doors, their discovery documents and `/metrics` are served
#: outside the contract (`include_in_schema=False`): no contract path names them yet, so their entries
#: are checked against the API's own route declarations instead, and cover them the day one enters the
#: contract. Later stories (S46-07, S47-14, S47-15) add their machine-only routes here.
MACHINE_ONLY_ROUTES: dict[str, str] = {
    "/webhooks/*": "a forge, a tracker, a pipeline, a deployer, Alertmanager or a plugin's server posts "
    "its signed events",
    "/internal/*": "a runner or an agent session calls it from its pod, with its run token",
    "/mcp": "the MCP door (ADR 0030): an external MCP client calls the organisation's tools over "
    "JSON-RPC; outside the contract",
    "/mcp/projects/{project}": "the MCP door narrowed to one project, for the same clients; outside the "
    "contract",
    "/.well-known/oauth-protected-resource": "the MCP door's protected-resource metadata, read by an MCP "
    "client before it asks for a token; outside the contract",
    "/a2a/orgs/{org}": "the A2A door (ADR 0078): another agent sends the organisation a task over "
    "JSON-RPC; outside the contract",
    "/a2a/orgs/{org}/.well-known/agent-card.json": "the A2A door's agent card, read by the calling agent; "
    "outside the contract",
    "/metrics": "the Prometheus scrape, read by the monitoring stack; outside the contract",
    "/orgs/{org}/mcp-gateway/jwks.json": "the MCP gateway reads the organisation's public assertion keys "
    "to verify the calls Choregos signs (ADR 0105); unauthenticated",
    "/healthz": "the liveness probe of the kubelet and of the load balancer",
    "/readyz": "the readiness probe",
    "/orgs/{org}/agents/{slug}/responses": "the direct-invocation door (ADR 0080 §10): a person's program "
    "speaks the Responses API to a prompt or hosted agent",
    "/orgs/{org}/agent-sessions/{session_id}": "the same door's session: its transcript and its close, "
    "for the program that opened it",
}

#: Paths people reach without a client calling them, or that stay an API gesture on purpose.
NOT_CALLED_BY_A_CLIENT: dict[str, str] = {
    "/auth/callback": "the identity provider's redirect after sign-in: the browser lands on it",
    "/github/setup": "the GitHub App's Setup URL: GitHub redirects the browser there after an installation",
    "/remote-credentials/oauth/callback": "the provider's redirect after an administrator authorised a "
    "remote agent's credential",
    "/orgs/{org}/hosted-sources/{sha256}": "reads an uploaded source zip back for the pipeline that "
    "uploaded it; the console shows the upload's own answer",
    "/projects/{id}/workflows/validate": "the project-scoped twin of /workflows/validate, which the console "
    "and the CLI call; its extra actor-group warning is for API callers",
    "/templates/{name}": "platform template authoring by API (getTemplate, updateTemplate), no screen or "
    "command yet: finding VargaFoundation/choregos#1075",
    "/orgs/{org}/aliases/{alias}": "platform administrator's API gesture; its console text lives in the "
    "enterprise repository (S42-14 STATUS row)",
    "/work-items/{id}/artifacts/{ref}": "one revision's metadata, for API callers; the console lists the "
    "revisions (/work-items/{id}/artifacts) and downloads one through .../{ref}/content",
    "/releases/{id}": "one release by id, for API callers; the console reads a project's releases "
    "(/projects/{id}/releases) and acts on one through /releases/{id}/approve and /abort",
    "/orgs/{org}/skills/{slug}/versions": "publishes a version from its files as JSON, for API callers; the "
    "console publishes through the zip import (/skills/import) and the git import (/skills/import-git)",
}

#: Paths whose console screen is a story still to come: that story removes its entries.
NO_CLIENT_YET: dict[str, str] = {
    "/orgs/{org}/mcp-gateway": "contract only until S46-08 serves it; console card: S46-13",
    "/orgs/{org}/mcp-gateway/test": "contract only until S46-08 serves it; console card: S46-13",
    "/orgs/{org}/mcp-gateway/assertion-key": "contract only until S46-07 serves it; console card: S46-13",
}

#: Paths the CLI calls and the console must not: an operator's command, not a screen.
CLI_ONLY: dict[str, str] = {
    "/platform/secrets/rewrap": "KEK rotation stays an operator command (`choregos secrets rewrap`)",
}

#: A path parameter as a client writes it: `${...}` in a TypeScript template literal, `{...}` in a
#: Python f-string, or a literal value (`/projects/p1/...`).
_PARAMETER = r"(?:\$\{[^}]*\}|\{[^}]*\}|[A-Za-z0-9_.:-]+)"

#: Where a client's path starts: right after the quote or backtick that opens its string, after the
#: console's `${API_BASE}`, or after the API's own `/api/v1` prefix written out.
_PATH_START = r"(?:(?<=[`\"'])|(?<=\$\{API_BASE\})|(?<=/api/v1))"

#: Where a client's path ends: the closing quote or backtick, a query string, or a `${...}` that appends
#: one (`${query(...)}`).
_PATH_END = r"(?=[`\"'?]|\$\{)"


def path_pattern(path: str) -> re.Pattern[str]:
    """What a client's call to `path` looks like in its source.

    Literal segments are escaped; a `{param}` segment is any of `_PARAMETER`. The match is the whole
    path a client writes: it starts the string (after its opening quote or backtick, or after
    `${API_BASE}` or `/api/v1`) and ends it (at its closing quote or backtick, a query string, or the
    `${...}` that appends one). A call to `/releases/{id}/abort` is not a call to `/releases/{id}`,
    nor is a call to `/orgs/{org}/agents/{slug}/releases/{id}/decision`.
    """
    segments = [
        _PARAMETER if segment.startswith("{") and segment.endswith("}") else re.escape(segment)
        for segment in path.strip("/").split("/")
    ]
    return re.compile(_PATH_START + "/" + "/".join(segments) + _PATH_END)


@functools.cache
def contract_paths() -> tuple[str, ...]:
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return tuple(spec["paths"])


def _read(files: Iterable[pathlib.Path]) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(files))


@functools.cache
def web_text() -> str:
    """Every TypeScript source of the console, its demo fixtures left out: they are not a client."""
    sources = (
        path
        for suffix in ("*.ts", "*.tsx")
        for path in WEB_SRC.rglob(suffix)
        if not path.is_relative_to(WEB_MOCKS)
    )
    return _read(sources)


@functools.cache
def cli_text() -> str:
    return _read(CLI_SRC.rglob("*.py"))


#: A route the API declares: a FastAPI route decorator or `add_api_route`, its path as first argument.
_ROUTE_DECLARATION = re.compile(
    r"(?:@\w+\.(?:get|post|put|patch|delete|api_route)|\.add_api_route)\(\s*\"(/[^\"]*)\""
)


def route_shape(path: str) -> str:
    """A path with its parameters' names left out: `/mcp/projects/{projet}` is `/mcp/projects/{}`."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def declared_routes(source: str) -> set[str]:
    """The shapes of the routes a Python source declares."""
    return {route_shape(path) for path in _ROUTE_DECLARATION.findall(source)}


@functools.cache
def api_routes() -> frozenset[str]:
    """Every route `apps/api/src` declares, as written there (a router's prefix is not added)."""
    return frozenset(declared_routes(_read(API_SRC.rglob("*.py"))))


def is_machine_only(path: str) -> bool:
    return any(
        path.startswith(entry.removesuffix("*"))
        if entry.endswith("/*")
        else route_shape(path) == route_shape(entry)
        for entry in MACHINE_ONLY_ROUTES
    )


def allowlisted(path: str) -> bool:
    return (
        is_machine_only(path) or path in NOT_CALLED_BY_A_CLIENT or path in NO_CLIENT_YET or path in CLI_ONLY
    )


def unexplained(paths: Iterable[str], web: str, cli: str) -> list[str]:
    """The paths that no client names and no allowlist holds."""
    return [
        path
        for path in paths
        if not allowlisted(path) and not path_pattern(path).search(web) and not path_pattern(path).search(cli)
    ]


def test_every_api_path_has_a_client() -> None:
    missing = unexplained(contract_paths(), web_text(), cli_text())
    assert not missing, (
        f"API paths that neither the console (apps/web/src) nor the CLI (packages/cli/src) calls: {missing}. "
        "Give each a client, or an entry with its reason in this file's allowlists."
    )


def test_the_matcher_sees_a_template_literal_an_fstring_and_a_literal() -> None:
    pattern = path_pattern("/projects/{id}/metrics/cross-backend")
    assert pattern.search("`/projects/${qualify(id)}/metrics/cross-backend${query()}`")
    assert pattern.search('f"/projects/{pid}/metrics/cross-backend"')
    assert pattern.search("'/projects/p1/metrics/cross-backend'")
    assert not pattern.search("/projects/p1/metrics/cross-backend-x")
    assert not pattern.search("/projects/p1/metrics/dora")
    assert not pattern.search("/projects/metrics/cross-backend")
    # The match is the whole path: a longer call, or the same segments inside another path, is not it.
    assert not pattern.search("`/projects/p1/metrics/cross-backend/sub`")
    assert not pattern.search("`/x/projects/p1/metrics/cross-backend`")
    assert not pattern.search("`/orgs/${org}/projects/${id}/metrics/cross-backend`")
    assert pattern.search("`${API_BASE}/projects/${id}/metrics/cross-backend`")
    assert pattern.search("`/api/v1/projects/${id}/metrics/cross-backend?window=7`")
    # A `{param}` segment is not satisfied by a literal sibling the client calls.
    assert not path_pattern("/releases/{id}").search("request(`/releases/${releaseId}/abort`)")


def test_a_new_client_less_path_is_refused() -> None:
    web = "request(`/projects/${qualify(id)}/metrics/dora`)"
    assert unexplained(["/projects/{id}/metrics/brand-new"], web, "") == ["/projects/{id}/metrics/brand-new"]
    assert unexplained(["/projects/{id}/metrics/dora"], web, "") == []
    assert unexplained(["/internal/runs/{id}/brand-new", "/healthz"], "", "") == []
    # A machine-only door that enters the contract is covered whatever its parameter is named.
    assert unexplained(["/mcp/projects/{slug}"], "", "") == []


def test_the_api_route_declarations_are_read() -> None:
    source = """
@router.api_route("/mcp/projects/{projet}", methods=["GET", "DELETE"])
@router.post("/a2a/orgs/{org}")
@app.get("/metrics", tags=["session"], include_in_schema=False)
app.add_api_route("/edition", current_edition, methods=["GET"], include_in_schema=False)
document = (await client.get("/not-a-route")).json()
"""
    assert declared_routes(source) == {"/mcp/projects/{}", "/a2a/orgs/{}", "/metrics", "/edition"}


def test_every_allowlist_entry_is_still_true() -> None:
    paths = set(contract_paths())
    served = api_routes()
    web, cli = web_text(), cli_text()
    lists = {
        "MACHINE_ONLY_ROUTES": MACHINE_ONLY_ROUTES,
        "NOT_CALLED_BY_A_CLIENT": NOT_CALLED_BY_A_CLIENT,
        "NO_CLIENT_YET": NO_CLIENT_YET,
        "CLI_ONLY": CLI_ONLY,
    }
    stale: list[str] = []
    for name, entries in lists.items():
        for entry, reason in entries.items():
            if not reason.strip():
                stale.append(f"{name}[{entry}] has no reason")
            if entry.endswith("/*"):
                if not any(path.startswith(entry.removesuffix("*")) for path in paths):
                    stale.append(f"{name}[{entry}] covers no path of the contract")
                continue
            if entry not in paths:
                if name == "MACHINE_ONLY_ROUTES":
                    if route_shape(entry) not in served:
                        stale.append(
                            f"{name}[{entry}] is neither a path of the contract nor a route of the API"
                        )
                    continue
                stale.append(f"{name}[{entry}] is not a path of the contract")
                continue
            pattern = path_pattern(entry)
            if name == "CLI_ONLY":
                if not pattern.search(cli) or pattern.search(web):
                    stale.append(f"{name}[{entry}] must be called by the CLI and not by the console")
            elif name != "MACHINE_ONLY_ROUTES" and (pattern.search(web) or pattern.search(cli)):
                stale.append(f"{name}[{entry}] has a client now: remove the entry")
    exact = [entry for entries in lists.values() for entry in entries]
    stale += [f"{entry} is in two allowlists" for entry in sorted({e for e in exact if exact.count(e) > 1})]
    assert not stale, stale
