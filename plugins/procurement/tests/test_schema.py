# SPDX-License-Identifier: Apache-2.0
"""The offers_structured schema: its examples validate, and what it should refuse is refused."""

from __future__ import annotations

import copy
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from tranibor_procurement.schemas import OFFERS_STRUCTURED, load_schema

SCHEMA = load_schema(OFFERS_STRUCTURED)
VALIDATOR = Draft202012Validator(SCHEMA)


def _example() -> dict[str, Any]:
    example: dict[str, Any] = copy.deepcopy(SCHEMA["examples"][1])
    return example


def test_the_schema_is_itself_valid_2020_12() -> None:
    Draft202012Validator.check_schema(SCHEMA)
    assert SCHEMA["$schema"].endswith("2020-12/schema")


@pytest.mark.parametrize("index", range(2))
def test_every_example_validates(index: int) -> None:
    assert not list(VALIDATOR.iter_errors(SCHEMA["examples"][index]))


def test_there_are_examples() -> None:
    assert len(SCHEMA["examples"]) == 2


def _extra_top(d: dict[str, Any]) -> None:
    d["currency"] = "EUR"


def _extra_supplier(d: dict[str, Any]) -> None:
    d["suppliers"][0]["rating"] = 5


def _extra_line(d: dict[str, Any]) -> None:
    d["suppliers"][0]["lines"][0]["tax"] = 1


def _lower_currency(d: dict[str, Any]) -> None:
    d["suppliers"][0]["currency"] = "eur"


def _missing_key(d: dict[str, Any]) -> None:
    del d["suppliers"][0]["stated_total"]


def _bad_date(d: dict[str, Any]) -> None:
    d["suppliers"][0]["validity_date"] = "31/12/2026"


def _string_amount(d: dict[str, Any]) -> None:
    d["suppliers"][0]["stated_total"] = "1250"


@pytest.mark.parametrize(
    "break_it",
    [_extra_top, _extra_supplier, _extra_line, _lower_currency, _missing_key, _bad_date, _string_amount],
)
def test_a_departure_from_the_shape_is_refused(break_it: Any) -> None:
    doc = _example()
    break_it(doc)
    assert list(VALIDATOR.iter_errors(doc))
