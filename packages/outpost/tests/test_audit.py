# SPDX-License-Identifier: Apache-2.0
"""The outpost's own audit log (ADR 0147 §8): the operator's record, comparable with the platform's."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from choregos_contracts.enums import OutpostAnswerOutcome, OutpostAuditEvent, OutpostRefusalCode
from choregos_contracts.outpost import OutpostAssertedNames, arguments_sha256
from choregos_outpost.audit import AuditLog, AuditWriteError, logged_names
from pydantic import ValidationError

from ._bench import AUDIT_LINE, bench


async def test_a_line_is_the_contracts_and_holds_digests_never_a_payload(
    tmp_path: Path, daemon_log: io.StringIO
) -> None:
    b = await bench(tmp_path, daemon_log, writes="allow")
    arguments = {"title": "Δ of 3 € on 請求書", "labels": ["bug", "prod"]}
    asserted = {
        "sub": "run:r-42",
        "org": "acme",
        "project": "billing-api",
        "run": "r-42",
        "agent": "triage",
        "approved_by": ["ada@example.org"],
        "a_name_a_later_door_adds": "ignored",
    }
    call = b.door.submit("tracker", "create_issue", arguments, asserted=asserted)
    await b.start()
    await b.door.answered(call.id)
    await b.stop()
    text = b.audit_path.read_text(encoding="utf-8")
    received, executed = b.lines(call.id)  # each line is checked against the contract's schema
    # The digest is the one the platform's `tool.called` event keeps for the same call.
    assert received["arguments_sha256"] == executed["arguments_sha256"] == arguments_sha256(arguments)
    assert "請求書" not in text and "bug" not in text and "create_issue done" not in text
    # The names the control plane asserted are logged as it wrote them, the ones the contract lists.
    assert received["asserted"] == {
        key: value for key, value in asserted.items() if key != "a_name_a_later_door_adds"
    }
    assert text.endswith("\n") and all(line.startswith('{"at":"') for line in text.splitlines())


async def test_a_line_the_contract_would_refuse_cannot_be_written(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "log" / "audit.jsonl")
    with pytest.raises(ValidationError):
        await log.append(OutpostAuditEvent.REFUSED, "call-1")  # a refusal without its code
    with pytest.raises(ValidationError):
        # A call refused for its signature was not opened: its line names no tool.
        await log.append(
            OutpostAuditEvent.REFUSED,
            "call-1",
            refusal=OutpostRefusalCode.SIGNATURE_REFUSED,
            tool="create_issue",
        )
    assert not (tmp_path / "log" / "audit.jsonl").exists()
    line = await log.append(
        OutpostAuditEvent.EXECUTED, "call-1", outcome=OutpostAnswerOutcome.RESULT, duration_ms=3
    )
    written = json.loads((tmp_path / "log" / "audit.jsonl").read_text(encoding="utf-8"))
    assert (
        not list(AUDIT_LINE.iter_errors(written))
        and written["outcome"] == "result"
        and line.call_id == "call-1"
    )


async def test_a_log_rotated_by_renaming_loses_no_line(tmp_path: Path) -> None:
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    await log.append(OutpostAuditEvent.RECEIVED, "before")
    path.rename(tmp_path / "audit.jsonl.1")
    await log.append(OutpostAuditEvent.RECEIVED, "after")
    assert json.loads(path.read_text(encoding="utf-8"))["call_id"] == "after"
    assert json.loads((tmp_path / "audit.jsonl.1").read_text(encoding="utf-8"))["call_id"] == "before"


async def test_a_line_the_disk_refuses_raises_and_says_which(tmp_path: Path) -> None:
    blocked = tmp_path / "a-file"
    blocked.write_text("", encoding="utf-8")
    log = AuditLog(blocked / "audit.jsonl")  # its directory cannot be made
    with pytest.raises(AuditWriteError) as refused:
        await log.append(OutpostAuditEvent.RECEIVED, "call-1", tool="search_issues")
    assert json.loads(refused.value.line)["call_id"] == "call-1"
    with pytest.raises(AuditWriteError):
        await log.check()


def test_only_the_names_the_contract_lists_are_kept() -> None:
    asserted = OutpostAssertedNames.model_validate(
        {"org": "acme", "tenant_secret": "x", "approved_by": ["ada"]}
    )
    kept = logged_names(asserted).model_dump(exclude_defaults=True)
    assert kept == {"org": "acme", "approved_by": ["ada"]}
    assert logged_names(None).model_dump(exclude_defaults=True) == {}
