# SPDX-License-Identifier: Apache-2.0
"""Named scenes of the connector fakes (ADR 0005): the datasets the eval matrix plays when it compares
the two doors of a run's tools, MCP and scripts (S57-09, ADR 0122 §9; the examples page of
`docs/plan/PLAN-2026-11-03-tool-scripts-examples.md`).

`scene(kind, name)` builds, each time anew, the REAL connector of a kind wired to a fresh fake holding
the scene's data, and hands back both with the operations the kind declares. Nothing is shared with the
process-wide fakes of `CHOREGOS_FAKES=1`: a case that proposes eight actions leaves nothing behind for
the next one, and a test reads the fake's state to prove that nothing was written.

| kind             | scene            | what it holds                                                   |
|:-----------------|:-----------------|:----------------------------------------------------------------|
| `firewall`       | `default`        | the netops eval's pfSense (3 rules, 2 aliases)                  |
| `firewall`       | `ssh-exposure`   | E1: 6 interfaces, 184 rules, 41 aliases, 3 exposures, 6 decoys   |
| `network_policy` | `default`        | the netops eval's cluster and its recorded Hubble trace         |
| `network_policy` | `dropped-flows`  | E2: 12 namespaces, 2,400 flows, 310 dropped                     |
| `identity`       | `offboarding`    | E4: one enabled account in six groups                           |
| `marketdata`     | `default`        | the synthetic bars of `FakeMarketData` (a walk seeded by symbol) |

S57-11a: the LIVE matrix plays an agent case on a deployment whose model is the real gateway and whose
connectors are these scenes. Its seeding declares, in a per-case eval project, organisation connectors of
the case's kinds (`scene_type`) whose configuration holds `fake_scene: <name>`; `bound_scene` is what the
API builds for such a connector instead of its type's adapter. Two guards, so that no normal connector is
ever served by a fake:

- the field is refused on every declaration and edit (`registry.validate_config`): only the seeding, which
  writes its rows itself, sets it;
- it is honoured only when the deployment serves eval fakes (`CHOREGOS_EVAL_FAKE_SCENES=1`, given to the
  API and the orchestrator); otherwise it is ignored and the connector is built as its type says, from its
  configuration, which holds no address: it reaches nothing.

#1378: a case's built-in web tools read the case's own pages the same way. Its fake web is a `web_search`
organisation connector of type `fake` whose configuration holds `fake_scene: web` and the case's `web:`
(`fake_web`: the search results by query, the pages by address); `bound_web_scene` is what the API serves
both tools from, for the projects that connector is turned on for — `web_search` the case's results,
`web_fetch` the case's pages, the injected one included, each host resolving to `WEB_ADDRESS` and no other
host resolving at all. The same two guards hold: `fake_scene` is refused on every declaration and edit, and
the scene is honoured only where the deployment serves eval fakes.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ..errors import AdapterError
from ..registry import FAKE_SCENE_FIELD, OperationSpec, spec_of

#: The deployment's switch: the API serves a `fake_scene` connector from its scene only when it is on.
EVAL_FAKE_SCENES_ENV = "CHOREGOS_EVAL_FAKE_SCENES"
#: The scene of a case's fake web (#1378), on a `web_search` connector: served by the web tools, not by
#: the broker.
WEB_SCENE = "web"
#: The configuration field beside `fake_scene: web` that holds the case's `web:` (`search`, `pages`).
WEB_FIELD = "fake_web"
#: The address every host of a fake web resolves to: a public one, so that the API checks a page's host as
#: it checks any; the scene's client never connects to it.
WEB_ADDRESS = "93.184.216.34"


@dataclass
class Scene:
    """A connector on a fake: what the broker would call, and the fake a test reads."""

    kind: str
    name: str
    connector: Any
    fake: Any
    operations: tuple[OperationSpec, ...]


def _pfsense(fake: Any) -> Any:
    from ..netops import PfSenseFirewall

    return PfSenseFirewall(
        base_url="https://pfsense.fake", api_key="fake-api-key", transport=fake.transport()
    )


def _firewall(name: str) -> tuple[Any, Any]:
    from . import netops

    fake = netops.ssh_exposure_firewall() if name == "ssh-exposure" else netops.FakePfSense()
    return _pfsense(fake), fake


def _network_policy(name: str) -> tuple[Any, Any]:
    from ..netops import CiliumNetwork, ReadOnlyKubernetes
    from . import netops

    if name == "dropped-flows":
        cluster, flows = netops.dropped_flows_cluster(), netops.dropped_flows_trace()
    else:
        cluster, flows = netops.FakeCiliumKubernetes(), netops.recorded_flows()
    kubernetes = ReadOnlyKubernetes(
        "https://kubernetes.fake", "fake-reader-token", transport=cluster.transport()
    )
    return CiliumNetwork(kubernetes, flows, None), cluster


def _identity(name: str) -> tuple[Any, Any]:
    from ..identity import EntraIdentity
    from .entra import FakeEntra, offboarding_directory

    fake = offboarding_directory() if name == "offboarding" else FakeEntra()
    connector = EntraIdentity(
        tenant_id="acme",
        client_id="choregos",
        client_secret="fake",
        administrative_unit_id=fake.unit,
        transport=fake.transport(),
    )
    return connector, fake


def _marketdata(name: str) -> tuple[Any, Any]:
    from .marketdata import FakeMarketData

    fake = FakeMarketData()
    return fake, fake


#: kind → (the type whose operations it declares, its scenes, the builder).
_KINDS: dict[str, tuple[str, frozenset[str], Callable[[str], tuple[Any, Any]]]] = {
    "firewall": ("pfsense", frozenset({"default", "ssh-exposure"}), _firewall),
    "network_policy": ("cilium", frozenset({"default", "dropped-flows"}), _network_policy),
    "identity": ("entra", frozenset({"default", "offboarding"}), _identity),
    "marketdata": ("composite", frozenset({"default"}), _marketdata),
}


def scenes() -> dict[str, list[str]]:
    """Every kind that has scenes, and their names."""
    return {kind: sorted(names) for kind, (_, names, _) in _KINDS.items()}


def scene(kind: str, name: str = "default") -> Scene:
    """A fresh connector of `kind` on a fake holding the scene `name`."""
    if kind not in _KINDS:
        raise AdapterError(f"no scene for the kind {kind!r}: {', '.join(sorted(_KINDS))} have scenes")
    type_name, names, build = _KINDS[kind]
    if name not in names:
        raise AdapterError(f"the kind {kind} has no scene {name!r}: {', '.join(sorted(names))}")
    spec = spec_of(kind, type_name)
    connector, fake = build(name)
    return Scene(kind, name, connector, fake, spec.operations if spec is not None else ())


def scene_type(kind: str) -> str:
    """The registered type whose real connector a kind's scenes run (`firewall` → `pfsense`): the type of
    the organisation connector the live matrix declares, whose operations and policies are that type's."""
    if kind not in _KINDS:
        raise AdapterError(f"no scene for the kind {kind!r}: {', '.join(sorted(_KINDS))} have scenes")
    return _KINDS[kind][0]


@dataclass
class WebScene:
    """A case's fake web: what `web_search` finds (`search`, a `FakeSearchBackend` holding the case's
    results by query) and what `web_fetch` reads (`pages`: address -> `{title, text}`), nothing else."""

    search: Any
    pages: dict[str, dict[str, str]]

    @property
    def hosts(self) -> list[str]:
        """The hosts of its pages: the only ones that resolve."""
        from urllib.parse import urlsplit

        return sorted({host for url in self.pages if (host := urlsplit(url).hostname)})


def web_scene(web: Mapping[str, Any]) -> WebScene:
    """A case's `web:` (`{search: {query: [{title, url, snippet}]}, pages: {url: {title, text}}}`) as a
    fresh fake web: the scripted matrix's and the live matrix's are built the same way."""
    from ..web_search.base import result
    from .web_search import FakeSearchBackend

    backend = FakeSearchBackend()
    backend.results = {
        str(asked): [
            made
            for hit in hits or []
            if (made := result(hit.get("title"), hit.get("url"), hit.get("snippet"))) is not None
        ]
        for asked, hits in (web.get("search") or {}).items()
    }
    pages = {
        str(url): {"title": str((page or {}).get("title", "")), "text": str((page or {}).get("text", ""))}
        for url, page in (web.get("pages") or {}).items()
    }
    return WebScene(backend, pages)


def binds_web_scene(config: Mapping[str, Any] | None) -> bool:
    """Whether a `web_search` connector's configuration binds its projects' web tools to a case's fake
    web, and the deployment honours it (`CHOREGOS_EVAL_FAKE_SCENES`)."""
    return (config or {}).get(FAKE_SCENE_FIELD) == WEB_SCENE and eval_fake_scenes_allowed()


def bound_web_scene(config: Mapping[str, Any] | None) -> WebScene | None:
    """The fake web a `web_search` connector's configuration binds its projects to (`fake_scene: web`,
    the case's `web:` in `fake_web`), built anew; `None` when it binds none, and when the deployment does
    not serve eval fakes — the fields are then ignored (#1378)."""
    if not binds_web_scene(config):
        return None
    web = (config or {}).get(WEB_FIELD)
    return web_scene(web if isinstance(web, Mapping) else {})


def eval_fake_scenes_allowed() -> bool:
    """Whether this deployment serves eval fakes (`CHOREGOS_EVAL_FAKE_SCENES=1`); off by default."""
    return os.environ.get(EVAL_FAKE_SCENES_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def bound_scene(kind: str, config: Mapping[str, Any] | None) -> Scene | None:
    """The scene a connector's configuration binds it to (`fake_scene`), built anew, or `None`: when the
    configuration names none, and when the deployment does not serve eval fakes — the field is then
    ignored, and the connector is built from its configuration as its type says (S57-11a)."""
    name = (config or {}).get(FAKE_SCENE_FIELD)
    if not name or name == WEB_SCENE or not eval_fake_scenes_allowed():
        # A fake web is served by the web tools (`bound_web_scene`), not by the broker.
        return None
    return scene(kind, str(name))


__all__ = [
    "EVAL_FAKE_SCENES_ENV",
    "FAKE_SCENE_FIELD",
    "WEB_ADDRESS",
    "WEB_FIELD",
    "WEB_SCENE",
    "Scene",
    "WebScene",
    "binds_web_scene",
    "bound_scene",
    "bound_web_scene",
    "eval_fake_scenes_allowed",
    "scene",
    "scene_type",
    "scenes",
    "web_scene",
]
