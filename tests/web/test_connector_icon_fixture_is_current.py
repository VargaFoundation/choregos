"""S40-03b: every connector type of the registry has an icon decision.

The console's icon test (`apps/web/tests/connector-icon.test.ts`) reads a copy of the registry's
`(kind, type)` pairs. This test keeps that copy equal to the registry: a type added without a
fixture update, hence without an icon decision, turns it red.

The registry is the one the API serves: the core's types and those of the installed extensions
(`investment-research` registers the `marketdata` family, S83-08), loaded as `create_app` loads them.
Without that, the test read whatever an earlier test happened to load, and failed alone.
"""

from __future__ import annotations

import json
import pathlib

from tranibor_adapters import connector_types, load_extensions

FIXTURE = (
    pathlib.Path(__file__).resolve().parents[2]
    / "apps"
    / "web"
    / "tests"
    / "fixtures"
    / "connector-types.json"
)


def test_the_fixture_equals_the_registry() -> None:
    load_extensions()
    expected = sorted((kind, type_name) for kind, type_name, _ in connector_types())
    actual = sorted((entry["kind"], entry["type"]) for entry in json.loads(FIXTURE.read_text()))
    assert actual == expected, (
        "regenerate apps/web/tests/fixtures/connector-types.json and decide the icon of the new type"
    )
