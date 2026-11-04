# SPDX-License-Identifier: Apache-2.0
"""Does an enterprise-edition extension, installed outside the tree, hold everything asked of it?

This is **the prerequisite of milestone E4** and of decision D-E1 (the enterprise edition lives in
a separate private repository). The whole edifice rests on one sentence: "the `choregos-ee`
package installs alongside, declares itself through a `choregos.plugins` entry point, and the
platform does the rest without being patched". As long as nobody has tried it, that sentence is
an intention.

What was already proven (`packages/adapters/tests/test_extensions.py`): a **connector** declared
by a real `.dist-info` is found by `importlib.metadata`, and an extension that breaks stops the
process. What was not, and it is the heart of the commercial model:

* **the edition**: `declare(ENTERPRISE)` called from the entry point, not from the test;
* **the start-up**: the API refuses `CHOREGOS_EDITION=enterprise` when nothing declared itself
  (#107). A real extension must therefore lift that refusal, otherwise the guard is a wall;
* **the second organisation**: the community edition's `409` must open;
* **the group mapper** (seam C3) and a **gate**, both through the entry point.

The earlier tests called `declare(...)` and `declare_group_mapper(...)` directly: they proved
that the functions work, not that the PATH works.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest

#: A credible `choregos-ee` in miniature: it makes the four gestures the enterprise edition will
#: really make, and nothing else. Written as a real module, because it will be one.
EE_EXTENSION = '''
from choregos_adapters import register
from choregos_api.edition import ENTERPRISE, declare, declare_group_mapper
from choregos_contracts import Role
from choregos_core.gates import GateOutcome, gate


class SandboxedExecutor:
    """An enterprise-edition executor (ADR 0016: cluster-scoped, so outside the CE)."""

    def __init__(self, config):
        self.config = config


def per_organisation_mapper(groups, default_org):
    """A prefix PER organisation: `acme/admin` IN ADDITION to `choregos:acme:admin`.

    It DELEGATES to the core's mapper for the rest, and that is not a courtesy: a mapper that
    replaces without understanding the core's format makes the deployment lose its own sign-ins.
    This extension's first draft did not delegate, and the development sign-in gave no role at
    all: `403 ['varga'] is missing` on the creation of an organisation.
    """
    from choregos_api.routers.auth import roles_from_groups

    roles = dict(roles_from_groups(groups, default_org))
    for group in groups:
        if "/" in group:
            org, name = group.split("/", 1)
            if name == "admin":
                roles[org] = Role.ORG_ADMIN
    return roles


@gate("signed_attestation")
def signed_attestation(ctx):
    """An enterprise-edition gate (ADR 0015)."""
    return GateOutcome(name="signed_attestation", passed=True, detail="signed")


def attach():
    declare(ENTERPRISE, features=frozenset({"multi_org", "attestation"}))
    declare_group_mapper(per_organisation_mapper)
    register("runtime", "agent-sandbox")(lambda cfg: SandboxedExecutor(cfg))
'''


def _install(root: pathlib.Path, name: str, source: str) -> None:
    """Writes a module and the `.dist-info` that declares it, as `pip install` would."""
    (root / f"{name}.py").write_text(source, encoding="utf-8")
    info = root / f"{name}-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: 0.1.0\n", encoding="utf-8")
    (info / "entry_points.txt").write_text(f"[choregos.plugins]\n{name} = {name}:attach\n", encoding="utf-8")


@pytest.fixture
def ee_extension_installed(tmp_path: pathlib.Path) -> Iterator[pathlib.Path]:
    """Installs the extension, and **cleans everything**: the edition and the mapper are module states."""
    from choregos_adapters import registry
    from choregos_api.edition import reset
    from choregos_core import gates

    # The extension registers a gate (`@gate`) in the core's module-level registries: snapshot them, or
    # the gate outlives the test and `packages/core` tests that expect the shipped gates only (and
    # every gate to have a summary) fail depending on which tests ran before.
    gates_before = (dict(gates._REGISTRY), set(gates._ASYNC_GATES), dict(gates._NEEDS))
    # Same for the connector types it registers: `tests/web` compares the registry to the console's
    # icon fixture, and a leftover `runtime/agent-sandbox` turned it red in a full run.
    connectors_before = (dict(registry._REGISTRY), dict(registry._SPECS))
    sys.path.insert(0, str(tmp_path))
    _install(tmp_path, "choregos_ee_fake", EE_EXTENSION)
    try:
        yield tmp_path
    finally:
        for target, before in zip(
            (gates._REGISTRY, gates._ASYNC_GATES, gates._NEEDS), gates_before, strict=True
        ):
            target.clear()
            target.update(before)  # type: ignore[arg-type]
        registry._REGISTRY.clear()
        registry._REGISTRY.update(connectors_before[0])
        registry._SPECS.clear()
        registry._SPECS.update(connectors_before[1])
        sys.path.remove(str(tmp_path))
        sys.modules.pop("choregos_ee_fake", None)
        reset()


def test_the_extension_declares_its_edition_through_the_entry_point(
    ee_extension_installed: pathlib.Path,
) -> None:
    """The gesture the whole model depends on: the edition comes from the PACKAGE, not from a test call."""
    from choregos_adapters import available, load_extensions
    from choregos_api.edition import current, features, is_enterprise

    assert not is_enterprise(), "the test would start from an already polluted state"

    loaded = load_extensions()
    assert "choregos_ee_fake" in loaded

    assert current() == "enterprise"
    assert is_enterprise()
    assert features() == frozenset({"multi_org", "attestation"})
    # And its enterprise-edition executor is available like any connector.
    assert "agent-sandbox" in available("runtime")


def test_the_api_starts_whereas_it_refused_without_an_extension(
    ee_extension_installed: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard of #107 must be a check, not a wall.

    It refuses `CHOREGOS_EDITION=enterprise` when no extension declared itself. If a real
    extension did not lift it, the guard would forbid the enterprise edition instead of checking
    it, and nobody would notice before having a `choregos-ee` to deploy.
    """
    from choregos_api.config import reset_settings_cache
    from choregos_api.edition import reset
    from choregos_api.main import _check_the_edition, create_app

    monkeypatch.setenv("CHOREGOS_EDITION", "enterprise")
    reset_settings_cache()
    try:
        # Without a loaded extension: refusal, with the cause named.
        reset()
        with pytest.raises(RuntimeError, match="choregos-ee"):
            _check_the_edition(__import__("choregos_api.config", fromlist=["get_settings"]).get_settings())
        # With one: the application builds.
        assert create_app() is not None
    finally:
        monkeypatch.delenv("CHOREGOS_EDITION", raising=False)
        reset_settings_cache()


@pytest.mark.asyncio
async def test_the_extension_opens_the_second_organisation(
    ee_extension_installed: pathlib.Path, client: Any, org: str, admin: str
) -> None:
    """The community edition's `409` is the only thing that was REMOVED from the CE (§2.2).

    It must therefore open through the extension, and through it alone.
    """
    from choregos_adapters import load_extensions

    load_extensions()
    response = await client.post("/api/v1/orgs", json={"slug": "second", "name": "Second"})
    assert response.status_code == 201, response.text


def test_the_extension_replaces_the_mapper_and_the_gate(ee_extension_installed: pathlib.Path) -> None:
    """Seam C3 and the gates seam, through the real PATH this time."""
    from choregos_adapters import load_extensions
    from choregos_api.edition import group_mapper
    from choregos_api.routers.auth import roles_from_groups
    from choregos_contracts import Role
    from choregos_core.gates import known_gates

    load_extensions()

    mapper = group_mapper(roles_from_groups)
    assert mapper is not roles_from_groups
    # The enterprise prefix, which the core cannot read.
    assert mapper(["acme/admin", "noise"], "varga") == {"acme": Role.ORG_ADMIN}
    # And the core does not understand that form, which proves the other one is the one serving.
    assert roles_from_groups(["acme/admin"], "varga") == {}
    # But the CORE's format keeps working: an enterprise mapper DELEGATES, otherwise the
    # deployment loses its own sign-ins (seen while writing this test: 403 on `POST /orgs`).
    assert mapper(["choregos:varga:org_admin"], "varga") == roles_from_groups(
        ["choregos:varga:org_admin"], "varga"
    )

    # The extension's gate is known to the validator: without that, a workflow that uses it is
    # invalid (`gate.unknown` is a blocking error) and nothing ever runs.
    assert "signed_attestation" in known_gates()
