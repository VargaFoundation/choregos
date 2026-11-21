# SPDX-License-Identifier: Apache-2.0
"""`tranibor-procurement supplier-check`: the facts of an onboarding file."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from tranibor_procurement.cli import main
from tranibor_procurement.compare import input_filename
from tranibor_procurement.relay import RelayError
from tranibor_procurement.schemas import SUPPLIER_FILE, load_schema

IDENTITY = {"legal_name": "Alderbrook Supplies Ltd", "registration_number": "GB-0001234", "country": "GB"}


def _doc(kind: str, expires_on: str | None) -> dict[str, Any]:
    return {
        "kind": kind,
        "file": f"{kind}.pdf",
        "issuer": "Northfield Mutual",
        "issued_on": "2025-01-01",
        "expires_on": expires_on,
        "identifier": "NM-1",
    }


def _hand(root: Path, documents: list[dict[str, Any]], **fields: str) -> None:
    doc = {"identity": IDENTITY, "documents": documents}
    (root / ".choregos" / "inputs" / input_filename(SUPPLIER_FILE)).write_text(json.dumps(doc))
    request = {"legal_name": IDENTITY["legal_name"], "registration_number": IDENTITY["registration_number"]}
    (root / ".choregos" / "fields.json").write_text(json.dumps({**request, **fields}))


def _job(root: Path) -> dict[str, Any]:
    done: dict[str, Any] = json.loads((root / ".choregos" / "job.json").read_text(encoding="utf-8"))
    return done


def test_the_schema_is_valid_and_its_example_validates() -> None:
    schema = load_schema(SUPPLIER_FILE)
    Draft202012Validator.check_schema(schema)
    assert not list(Draft202012Validator(schema).iter_errors(schema["examples"][0]))


def test_one_expired_and_one_missing_document(workspace: Path) -> None:
    _hand(workspace, [_doc("insurance", "2026-03-01"), _doc("tax-certificate", "2027-01-01")])
    argv = ["supplier-check", "--required", "insurance", "tax-certificate", "--required", "licence"]
    assert main([*argv, "--as-of", "2026-11-20"]) == 0
    done = _job(workspace)
    assert done["facts"] == {"documents_missing": 1, "documents_expired": 1, "identity_mismatch": 0}
    check = done["outputs"]["supplier_check"]
    assert check["missing"] == ["licence"]
    assert check["expired"] == [{"kind": "insurance", "file": "insurance.pdf", "expires_on": "2026-03-01"}]
    assert "NM-1" not in json.dumps(done)


def test_a_document_that_expires_on_the_day_is_valid_and_one_without_expiry_never_expires(
    workspace: Path,
) -> None:
    _hand(workspace, [_doc("insurance", "2026-11-20"), _doc("licence", None)])
    assert main(["supplier-check", "--required", "insurance", "licence", "--as-of", "2026-11-20"]) == 0
    assert _job(workspace)["facts"]["documents_expired"] == 0


def test_a_valid_copy_beside_an_expired_one_is_enough(workspace: Path) -> None:
    _hand(workspace, [_doc("insurance", "2020-01-01"), _doc("Insurance", "2027-01-01")])
    assert main(["supplier-check", "--required", "insurance", "--as-of", "2026-11-20"]) == 0
    assert _job(workspace)["facts"]["documents_expired"] == 0


def test_an_identity_that_differs_by_case_only_is_no_mismatch(workspace: Path) -> None:
    _hand(workspace, [], legal_name="ALDERBROOK  supplies, ltd.", registration_number="gb 0001234")
    assert main(["supplier-check", "--required", "x", "--as-of", "2026-11-20"]) == 0
    assert _job(workspace)["facts"]["identity_mismatch"] == 0


def test_an_identity_that_differs_by_one_digit_is_a_mismatch(workspace: Path) -> None:
    _hand(workspace, [], registration_number="GB-0001235")
    assert main(["supplier-check", "--required", "x", "--as-of", "2026-11-20"]) == 0
    done = _job(workspace)
    assert done["facts"]["identity_mismatch"] == 1
    assert done["outputs"]["supplier_check"]["identity_mismatch"][0]["field"] == "registration_number"


def test_a_missing_field_is_a_broken_contract(workspace: Path) -> None:
    _hand(workspace, [])
    (workspace / ".choregos" / "fields.json").write_text("{}")
    assert main(["supplier-check", "--required", "x", "--as-of", "2026-11-20"]) == 2
    assert not (workspace / ".choregos" / "job.json").exists()


def test_no_required_kind_and_a_bad_date_are_refused(workspace: Path) -> None:
    _hand(workspace, [])
    for argv in (
        ["supplier-check", "--as-of", "today"],
        ["supplier-check", "--required", "x", "--as-of", "soon"],
    ):
        with pytest.raises(SystemExit) as exc:
            main(argv)
        assert exc.value.code != 0


def test_a_duplicate_found(workspace: Path) -> None:
    _hand(workspace, [], country="GB")
    seen: list[tuple[str, dict[str, Any]]] = []

    def relay(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        seen.append((operation, arguments))
        return {"suppliers": [{"id": "s-9", "name": "Alderbrook Supplies Limited", "iban": "x"}]}

    argv = ["supplier-check", "--required", "x", "--as-of", "2026-11-20", "--duplicates"]
    assert main(argv, relay=relay) == 0
    done = _job(workspace)
    assert done["facts"]["duplicates_found"] == 1
    assert done["outputs"]["supplier_check"]["duplicates"] == [
        {"id": "s-9", "name": "Alderbrook Supplies Limited"}
    ]
    assert seen[0][0] == "search_suppliers"
    assert seen[0][1]["name"] == IDENTITY["legal_name"] and seen[0][1]["country"] == "GB"
    assert "iban" not in json.dumps(done)


def test_without_the_flag_the_relay_is_not_called_and_no_duplicate_fact_exists(workspace: Path) -> None:
    _hand(workspace, [])

    def relay(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("not asked")

    assert main(["supplier-check", "--required", "x", "--as-of", "2026-11-20"], relay=relay) == 0
    assert "duplicates_found" not in _job(workspace)["facts"]


def test_a_relay_that_fails_is_a_non_zero_exit_never_zero_duplicates(workspace: Path) -> None:
    _hand(workspace, [])

    def relay(operation: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise RelayError("purchasing__search_suppliers: down")

    argv = ["supplier-check", "--required", "x", "--as-of", "2026-11-20", "--duplicates"]
    assert main(argv, relay=relay) != 0
    assert not (workspace / ".choregos" / "job.json").exists()
