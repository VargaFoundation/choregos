"""CLI fixtures: no test meets the version check (S62-08) unless it asks for it."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

try:  # pytest-textual-snapshot 1.0 names the extension `_file_extension`; syrupy 5+ reads `file_extension`
    from pytest_textual_snapshot import SVGImageExtension

    SVGImageExtension.file_extension = "svg"
except ImportError:  # the plugin is a test dependency of the workspace's snapshots only
    pass

OPENAPI = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "openapi.yaml"


@pytest.fixture(autouse=True)
def _no_real_profile(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    # `client.CONFIG_PATH` is read from the environment when the module is first imported: a suite that
    # imports it at collection (the workspace's) would freeze the developer's real profile for the ones
    # that import it later. A suite that wants a profile file points `CONFIG_PATH` at its own.
    from tranibor_cli import client

    monkeypatch.setattr(client, "CONFIG_PATH", tmp_path / "no-profile.json")


@pytest.fixture(autouse=True)
def _no_version_check(monkeypatch: pytest.MonkeyPatch) -> None:
    # The check reads `GET /edition` once a process and writes under ~/.cache: the suites mock
    # their own routes and must not write there. `test_self_update.py` turns it back on.
    monkeypatch.setenv("CHOREGOS_NO_VERSION_CHECK", "1")


@pytest.fixture(scope="session")
def contract() -> Any:
    """`contract(name, payload)`: the payload a test's fake API serves, validated against `openapi.yaml`'s
    schema of that name, so a fake cannot drift from what the platform answers (ADR 0005)."""
    import jsonschema
    import yaml
    from referencing import Registry, Resource
    from referencing.jsonschema import DRAFT202012

    document = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    registry = Registry().with_resource("openapi.yaml", Resource.from_contents(document, DRAFT202012))

    def check(name: str, payload: Any) -> Any:
        schema = {"$ref": f"openapi.yaml#/components/schemas/{name}"}
        jsonschema.Draft202012Validator(schema, registry=registry).validate(payload)
        return payload

    return check
