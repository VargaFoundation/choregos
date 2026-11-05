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


def eval_fake_scenes_allowed() -> bool:
    """Whether this deployment serves eval fakes (`CHOREGOS_EVAL_FAKE_SCENES=1`); off by default."""
    return os.environ.get(EVAL_FAKE_SCENES_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def bound_scene(kind: str, config: Mapping[str, Any] | None) -> Scene | None:
    """The scene a connector's configuration binds it to (`fake_scene`), built anew, or `None`: when the
    configuration names none, and when the deployment does not serve eval fakes — the field is then
    ignored, and the connector is built from its configuration as its type says (S57-11a)."""
    name = (config or {}).get(FAKE_SCENE_FIELD)
    if not name or not eval_fake_scenes_allowed():
        return None
    return scene(kind, str(name))


__all__ = [
    "EVAL_FAKE_SCENES_ENV",
    "FAKE_SCENE_FIELD",
    "Scene",
    "bound_scene",
    "eval_fake_scenes_allowed",
    "scene",
    "scene_type",
    "scenes",
]
