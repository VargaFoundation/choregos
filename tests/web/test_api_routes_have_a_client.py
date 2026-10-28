"""S47-17: every path of the API contract has a client, or a stated reason not to.

The console (`apps/web/src`, without its demo fixtures under `src/mocks`, which answer calls and
make none) and the CLI (`packages/cli/src`) are the platform's clients. A path neither of them
names is a feature nobody reaches without `curl`: the cross-backend review metric and the memory
re-import were such features until this story gave them a screen. The guard reads the contract's
paths and fails for one that no client names, unless one of the allowlists below holds it with its
reason. An entry that is no longer true (the path left the contract, or a client calls it now) fails
too: the allowlists say what is client-less today, not what was.

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

#: Routes only machines call, never a screen or a command. An entry ending in `/*` covers every path
#: under that prefix. The MCP door (`/mcp`, `/mcp/projects/{project}`), the A2A door
#: (`/a2a/orgs/{org}` and its agent card) and `/metrics` are machine-only too, but the API serves them
#: outside the contract (`include_in_schema=False`): no contract path names them, so they need an entry
#: only when one enters the contract. Later stories add their machine-only routes here.
MACHINE_ONLY_ROUTES: dict[str, str] = {
    "/webhooks/*": "a forge, a tracker, a pipeline, a deployer, Alertmanager or a plugin's server posts "
    "its signed events",
    "/internal/*": "a runner or an agent session calls it from its pod, with its run token",
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
    "command; finding filed by S47-17",
    "/orgs/{org}/aliases/{alias}": "platform administrator's API gesture; its console text lives in the "
    "enterprise repository (S42-14 STATUS row)",
}

_PLUGIN_CONSOLE = (
    'console screen: plugin-plan S43-08 "Console: Catalogue -> Plugins" '
    "(docs/plan/PLAN-2026-10-27-servicenow.md)"
)

#: Paths whose console screen is a story still to come: that story removes its entries.
NO_CLIENT_YET: dict[str, str] = {
    "/orgs/{org}/memory/ab-report": "console screen: S47-18",
    "/platform/gateway/keys": "console screen: S47-18",
    "/orgs/{org}/plugins": _PLUGIN_CONSOLE,
    "/orgs/{org}/plugins/install": _PLUGIN_CONSOLE,
    "/orgs/{org}/plugins/{name}": _PLUGIN_CONSOLE,
    "/orgs/{org}/plugins/{name}/upgrade": _PLUGIN_CONSOLE,
    "/orgs/{org}/plugin-sources": _PLUGIN_CONSOLE,
    "/orgs/{org}/plugin-approvals": _PLUGIN_CONSOLE,
    "/orgs/{org}/plugin-approvals/{approval_id}/approve": _PLUGIN_CONSOLE,
}

#: Paths the CLI calls and the console must not: an operator's command, not a screen.
CLI_ONLY: dict[str, str] = {
    "/platform/secrets/rewrap": "KEK rotation stays an operator command (`choregos secrets rewrap`)",
}

#: A path parameter as a client writes it: `${...}` in a TypeScript template literal, `{...}` in a
#: Python f-string, or a literal value (`/projects/p1/...`).
_PARAMETER = r"(?:\$\{[^}]*\}|\{[^}]*\}|[A-Za-z0-9_.:-]+)"


def path_pattern(path: str) -> re.Pattern[str]:
    """What a client's call to `path` looks like in its source.

    Literal segments are escaped; a `{param}` segment is any of `_PARAMETER`. The match ends where a
    path segment cannot go on: `/metrics/cross-backend` is not `/metrics/cross-backend-x`.
    """
    segments = [
        _PARAMETER if segment.startswith("{") and segment.endswith("}") else re.escape(segment)
        for segment in path.strip("/").split("/")
    ]
    return re.compile("/" + "/".join(segments) + r"(?![A-Za-z0-9_-])")


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


def is_machine_only(path: str) -> bool:
    return any(
        path.startswith(entry.removesuffix("*")) if entry.endswith("/*") else path == entry
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
    assert pattern.search("/projects/p1/metrics/cross-backend")
    assert not pattern.search("/projects/p1/metrics/cross-backend-x")
    assert not pattern.search("/projects/p1/metrics/dora")
    assert not pattern.search("/projects/metrics/cross-backend")


def test_a_new_client_less_path_is_refused() -> None:
    web = "request(`/projects/${qualify(id)}/metrics/dora`)"
    assert unexplained(["/projects/{id}/metrics/brand-new"], web, "") == ["/projects/{id}/metrics/brand-new"]
    assert unexplained(["/projects/{id}/metrics/dora"], web, "") == []
    assert unexplained(["/internal/runs/{id}/brand-new", "/healthz"], "", "") == []


def test_every_allowlist_entry_is_still_true() -> None:
    paths = set(contract_paths())
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
