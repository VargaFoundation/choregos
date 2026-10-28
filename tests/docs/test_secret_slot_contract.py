# SPDX-License-Identifier: Apache-2.0
"""A secret field's slots, in the contract and in the routes (S39-30a, ADR 0089).

A pooled field `api_key` takes slots `api_key.2` … `api_key.10`, written and cleared through the same
`…/secrets/{field}` routes. The contract widens only the `{field}` pattern; the routes must accept
exactly what it says, or a client following the contract gets a 422 the contract does not announce.
"""

from __future__ import annotations

import pathlib
import re

import pytest
import yaml
from choregos_api.services.connector_secrets import SECRET_FIELD

ROOT = pathlib.Path(__file__).resolve().parents[2]
OPENAPI = yaml.safe_load((ROOT / "packages/contracts/openapi.yaml").read_text(encoding="utf-8"))
ROUTES = (
    "/orgs/{org}/connectors/{name}/secrets/{field}",
    "/projects/{id}/connectors/{kind}/secrets/{field}",
)


def _field_pattern(path: str) -> str:
    parameters = OPENAPI["paths"][path]["parameters"]
    (field,) = [p for p in parameters if isinstance(p, dict) and p.get("name") == "field"]
    return str(field["schema"]["pattern"])


@pytest.mark.parametrize("path", ROUTES)
def test_the_contract_and_the_routes_accept_the_same_fields(path: str) -> None:
    assert path in OPENAPI["paths"], path
    assert _field_pattern(path) == SECRET_FIELD


@pytest.mark.parametrize(
    ("field", "accepted"),
    [
        ("api_key", True),
        ("api_key.2", True),
        ("api_key.10", True),
        ("api_key.1", False),
        ("api_key.11", False),
        ("api_key.02", False),
        ("Api_key", False),
    ],
)
def test_a_slot_is_two_to_ten(field: str, accepted: bool) -> None:
    assert (re.fullmatch(SECRET_FIELD.strip("^$"), field) is not None) is accepted


def test_the_decision_is_listed() -> None:
    index = (ROOT / "docs/adr/README.md").read_text(encoding="utf-8")
    assert "(0089-a-secret-field-holds-several-keys.md)" in index


@pytest.mark.parametrize(
    ("schema", "model"),
    [
        ("ConnectorType", "projets.ConnectorType"),
        ("ConnectorCatalogueEntry", "catalogues.ConnectorCatalogueEntryDto"),
    ],
)
def test_a_type_says_which_fields_it_pools(schema: str, model: str) -> None:
    """The console offers *add a key* only where the type pools the field (S39-30c)."""
    import importlib

    assert "pooled_fields" in OPENAPI["components"]["schemas"][schema]["properties"]
    module, name = model.split(".")
    dto = getattr(importlib.import_module(f"choregos_api.schemas.{module}"), name)
    assert "pooled_fields" in dto.model_fields
