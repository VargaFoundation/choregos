# SPDX-License-Identifier: Apache-2.0
"""S47-34: the adapters package root keeps only its public API, and the registry did not change.

The connector type specs live in `specs.py` and the builtin registrations in `builtins.py`. The
registered `(kind, type)` pairs, in registration order, with what each declares, are pinned against
a fixture captured from the single-file `__init__.py` before it was split. The names of the package
root, and the identity of its mutable fake singletons, are pinned too.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import choregos_adapters
import pytest
from choregos_adapters import registry

ROOT = Path(choregos_adapters.__file__).parent / "__init__.py"
FIXTURE = Path(__file__).parent / "fixtures" / "connector_registry.json"
MAX_LINES = 300

#: The names the package root exposed before the split (tests, the API and the worker read them).
ROOT_NAMES = (
    "EXTENSION_GROUP",
    "FAKE_ENTRA",
    "FAKE_MARKETDATA",
    "FAKE_MCP",
    "FAKE_MCP_GATEWAY",
    "FAKE_NETOPS",
    "FAKE_WEB_SEARCH",
    "FAUX_ENTRA",
    "FAUX_MARKETDATA",
    "FAUX_METIER",
    "FAUX_WEB_SEARCH",
    "GATEWAY_SERVER_PATTERN",
    "GROUPE_DE_GREFFONS",
    "MCP_GATEWAY_TYPE",
    "POLITIQUES",
    "_SHIPPED_SPECS",
    "_SPECS_LIVREES",
    "_github_client",
    "_jira_tracker",
    "_register_builtins",
    "charger_les_greffons",
    "configuration_resolue",
    "fake_mcp_gateway",
    "load_extensions",
    "type_par_defaut",
)


def describe_registry() -> list[dict[str, Any]]:
    """Every registered pair, in registration order, with what its spec declares."""
    described = []
    for (kind, type_name), spec in registry._SPECS.items():
        described.append(
            {
                "kind": kind,
                "type": type_name,
                "display": spec.display,
                "capabilities": list(spec.capabilities),
                "config_schema": spec.config_schema,
                "secret_fields": list(spec.secret_fields),
                "deprecated": spec.deprecated,
                "pooled_fields": list(spec.pooled_fields),
                "dev_only": spec.dev_only,
                "has_egress": spec.egress is not None,
                "has_check_config": spec.check_config is not None,
                "has_factory": (kind, type_name) in registry._REGISTRY,
                "operations": [
                    {
                        "name": op.name,
                        "access": op.access,
                        "human_approval": op.human_approval,
                        "platform_arguments": list(op.platform_arguments),
                        "input_schema": op.input_schema,
                        "compensation": op.compensation,
                    }
                    for op in spec.operations
                ],
            }
        )
    return described


def test_the_package_root_stays_small() -> None:
    lines = len(ROOT.read_text().splitlines())
    assert lines <= MAX_LINES, (
        f"{ROOT.name} has {lines} lines: specs go to specs.py, registrations to builtins.py"
    )


def test_the_registry_is_unchanged() -> None:
    expected = json.loads(FIXTURE.read_text())
    actual = json.loads(json.dumps(describe_registry()))
    assert [(e["kind"], e["type"]) for e in actual] == [(e["kind"], e["type"]) for e in expected]
    assert actual == expected


@pytest.mark.parametrize("name", ROOT_NAMES)
def test_the_root_still_exposes_its_names(name: str) -> None:
    assert hasattr(choregos_adapters, name)


def test_the_spec_table_is_one_object() -> None:
    from choregos_adapters import specs

    assert choregos_adapters._SHIPPED_SPECS is specs._SHIPPED_SPECS
    assert choregos_adapters._SPECS_LIVREES is specs._SHIPPED_SPECS


def test_a_rebound_fake_is_the_one_the_factories_use(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests rebind `choregos_adapters.FAKE_*`: the moved factories must read the package attribute."""
    from choregos_adapters import builtins
    from choregos_adapters.fakes.mcp import FakeMcpServer

    server = FakeMcpServer()
    monkeypatch.setattr(choregos_adapters, "FAKE_MCP", server)
    assert builtins._current_fake_mcp() is server

    monkeypatch.setattr(choregos_adapters, "FAKE_NETOPS", {"pfsense": "scripted"})
    assert builtins._fake_netops("pfsense") == "scripted"


def test_a_fake_singleton_is_created_on_the_package(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_adapters import builtins

    monkeypatch.setattr(choregos_adapters, "FAKE_WEB_SEARCH", None)
    created = builtins._fake_web_search({})
    assert choregos_adapters.FAKE_WEB_SEARCH is created
    assert choregos_adapters.FAUX_WEB_SEARCH is created  # lang: fr-ok alias:internal
