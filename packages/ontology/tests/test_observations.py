# SPDX-License-Identifier: Apache-2.0
"""The observations NDJSON v1 format and its sync plan (trial element 2, spec 30 §13.2)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from choregos_ontology.observations import PartialReportError, parse_report, plan_sync

NOW = "2026-10-03T12:00:00+00:00"


def report(*lines: dict[str, Any], end: dict[str, Any] | None = None) -> str:
    body = [json.dumps(line) for line in lines]
    body.append(json.dumps(end if end is not None else {"_end": True, "lines": len(lines)}))
    return "\n".join(body) + "\n"


def line(check: str, scope: str, status: str, **extra: Any) -> dict[str, Any]:
    return {"layer": "os", "check": check, "scope": scope, "status": status, **extra}


def sync(current: dict[str, dict[str, Any]], text: str) -> dict[str, dict[str, Any]]:
    """Apply the plan to ``current`` and return the new state, as the route does."""
    plan = plan_sync(current, parse_report(text), now=NOW)
    return {**current, **plan.create, **plan.update}


REBOOT = report(
    line("reboot-required", "node-1", "finding", severity="medium", observed_at="2026-10-03T08:00:00Z"),
    line("reboot-required", "node-2", "finding", severity="high", observed_at="2026-10-03T08:00:01Z"),
    line("reboot-required", "node-3", "ok", observed_at="2026-10-03T08:00:02Z"),
    line("disk-usage", "node-1", "ok", value=41.5, observed_at="2026-10-03T08:00:03Z"),
)


# ───────────────────────────── parsing ─────────────────────────────


def test_a_complete_report_is_parsed() -> None:
    observations = parse_report(REBOOT)
    assert [o.key for o in observations] == ["os-reboot-required"] * 3 + ["os-disk-usage"]
    assert observations[0].observed_at == "2026-10-03T08:00:00+00:00"


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "empty report"),
        (report(line("c", "s", "ok"), end={"lines": 1}), "truncated"),
        (json.dumps(line("c", "s", "ok")) + "\n", "truncated"),
        (report(line("c", "s", "ok"), end={"_end": True, "lines": 2}), "announces 2"),
        ('{"layer": "os", "check": \n' + json.dumps({"_end": True, "lines": 1}), "not JSON"),
        (report(line("c", "s", "broken")), "unknown status"),
        (report(line("c", "s", "finding", severity="urgent")), "unknown severity"),
        (report(line("c", "s", "finding", evidence="x" * 4097)), "4 KiB"),
        (report(line("c", "s", "ok", value={"nested": 1})), "value must be"),
        (report(line("c", "s", "ok", observed_at="yesterday")), "RFC 3339"),
        (report(line("c", "s", "ok", observed_at="2026-10-03T08:00:00")), "RFC 3339"),
        (report({"layer": "os", "check": "c", "status": "ok"}), "missing or non-string scope"),
        (
            report(
                {"layer": "os-a", "check": "b", "scope": "s", "status": "ok"},
                {"layer": "os", "check": "a-b", "scope": "s", "status": "ok"},
            ),
            "is produced by",
        ),
    ],
)
def test_a_partial_or_invalid_report_is_refused_whole(text: str, reason: str) -> None:
    with pytest.raises(PartialReportError, match=reason):
        parse_report(text)


# ───────────────────────────── sync plan ─────────────────────────────


def test_only_finding_lines_open_a_finding_grouped_by_key() -> None:
    state = sync({}, REBOOT)
    assert set(state) == {"os-reboot-required"}, "an `ok`-only check opens nothing"
    finding = state["os-reboot-required"]
    assert finding["status"] == "open"
    assert finding["scopes"] == ["node-1", "node-2"], "one finding per (layer, check), all its scopes"
    assert finding["severity"] == "high", "the worst severity of its lines"
    assert finding["source"] == finding["layer"] == "os"
    assert finding["first_seen"] == "2026-10-03T08:00:00+00:00"
    assert finding["last_seen"] == "2026-10-03T08:00:01+00:00"


def test_replaying_the_same_report_changes_nothing() -> None:
    state = sync({}, REBOOT)
    plan = plan_sync(state, parse_report(REBOOT), now="2026-10-04T00:00:00+00:00")
    assert plan.empty
    assert plan.summary() == {"created": 0, "updated": 0, "resolved": 0, "unchanged": 1}


def test_replaying_an_undated_report_changes_nothing_either() -> None:
    undated = report(line("ntp", "node-1", "finding"))
    state = sync({}, undated)
    assert plan_sync(state, parse_report(undated), now="2026-10-05T00:00:00+00:00").empty


def test_a_scope_is_resolved_only_by_ok_on_the_same_scope() -> None:
    state = sync({}, REBOOT)
    state = sync(state, report(line("reboot-required", "node-1", "ok", observed_at="2026-10-03T09:00:00Z")))
    finding = state["os-reboot-required"]
    assert finding["status"] == "open"
    assert finding["scopes"] == ["node-2"]


def test_a_finding_without_open_scope_is_resolved() -> None:
    state = sync({}, REBOOT)
    state = sync(
        state,
        report(
            line("reboot-required", "node-1", "ok", observed_at="2026-10-03T09:00:00Z"),
            line("reboot-required", "node-2", "ok", observed_at="2026-10-03T09:00:05Z"),
        ),
    )
    finding = state["os-reboot-required"]
    assert finding["status"] == "resolved"
    assert finding["scopes"] == []
    assert finding["resolved_at"] == "2026-10-03T09:00:05+00:00"


def test_unreachable_resolves_nothing() -> None:
    state = sync({}, REBOOT)
    plan = plan_sync(state, parse_report(report(line("reboot-required", "node-1", "unreachable"))), now=NOW)
    assert plan.empty


def test_an_absent_layer_resolves_nothing() -> None:
    state = sync({}, REBOOT)
    other_layer = report({"layer": "k8s", "check": "pods", "scope": "ns/a", "status": "ok"})
    assert plan_sync(state, parse_report(other_layer), now=NOW).empty


def test_a_contradiction_in_one_report_keeps_the_scope_open() -> None:
    state = sync({}, REBOOT)
    contradiction = report(
        line("reboot-required", "node-1", "ok"),
        line("reboot-required", "node-1", "finding"),
    )
    assert sync(state, contradiction)["os-reboot-required"]["scopes"] == ["node-1", "node-2"]


def test_a_resolved_finding_reopens() -> None:
    state = sync({}, report(line("ntp", "node-1", "finding", observed_at="2026-10-01T00:00:00Z")))
    state = sync(state, report(line("ntp", "node-1", "ok", observed_at="2026-10-02T00:00:00Z")))
    assert state["os-ntp"]["status"] == "resolved"
    state = sync(state, report(line("ntp", "node-4", "finding", observed_at="2026-10-03T00:00:00Z")))
    finding = state["os-ntp"]
    assert (finding["status"], finding["scopes"], finding["resolved_at"]) == ("open", ["node-4"], None)
    assert finding["first_seen"] == "2026-10-03T00:00:00+00:00", "a reopened finding starts a new episode"


def test_timestamps_with_offsets_compare_in_utc() -> None:
    state = sync(
        {},
        report(
            line("ntp", "a", "finding", observed_at="2026-10-03T10:00:00+02:00"),  # 08:00Z
            line("ntp", "b", "finding", observed_at="2026-10-03T09:00:00Z"),
        ),
    )
    assert state["os-ntp"]["last_seen"] == "2026-10-03T09:00:00+00:00"
