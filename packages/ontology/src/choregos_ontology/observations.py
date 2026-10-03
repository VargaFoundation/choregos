# SPDX-License-Identifier: Apache-2.0
"""Health reports in the ``observations`` NDJSON v1 format, and their sync plan (trial element 2).

One line per observation: ``{layer, check, scope, status: ok|finding|unreachable, value?, evidence?,
severity?, observed_at?}``; the last line is ``{"_end": true, "lines": N}``. A report is **partial** if a
line is not JSON, is invalid, or if ``_end`` is missing or counts wrong: then nothing is written.

Rules of the sync (spec 30 §13.2):
- only a ``finding`` line opens (or reopens) a finding; its key is ``<layer>-<check>``;
- a scope is resolved only by an ``ok`` line on the same (layer, check, scope);
- an absent layer, or an ``unreachable`` line, resolves nothing;
- a finding with no open scope left is ``resolved``.

The plan is computed before any write, and replaying the same report yields an empty plan.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

MAX_EVIDENCE_BYTES = 4096
STATUSES = frozenset({"ok", "finding", "unreachable"})
SEVERITIES = ("info", "low", "medium", "high", "critical")


class PartialReportError(ValueError):
    """The report cannot be trusted as complete; the reason says why. Nothing must be written."""


@dataclass(frozen=True, slots=True)
class Observation:
    layer: str
    check: str
    scope: str
    status: str
    value: Any = None
    evidence: str | None = None
    severity: str | None = None
    observed_at: str | None = None

    @property
    def key(self) -> str:
        return f"{self.layer}-{self.check}"


def _utc(value: Any) -> str | None:
    """An RFC 3339 timestamp, normalised to UTC so that two of them compare as strings; else None."""
    if not isinstance(value, str) or "T" not in value.upper():
        return None
    try:
        moment = datetime.fromisoformat(value.replace("z", "Z"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        return None
    return moment.astimezone(UTC).isoformat()


def _observation(raw: Any, number: int) -> Observation:
    if not isinstance(raw, dict):
        raise PartialReportError(f"line {number}: not an object")
    missing = [k for k in ("layer", "check", "scope", "status") if not isinstance(raw.get(k), str)]
    if missing:
        raise PartialReportError(f"line {number}: missing or non-string {', '.join(missing)}")
    if raw["status"] not in STATUSES:
        raise PartialReportError(f"line {number}: unknown status {raw['status']!r}")
    severity = raw.get("severity")
    if severity is not None and severity not in SEVERITIES:
        raise PartialReportError(f"line {number}: unknown severity {severity!r}")
    evidence = raw.get("evidence")
    too_long = isinstance(evidence, str) and len(evidence.encode()) > MAX_EVIDENCE_BYTES
    if evidence is not None and (not isinstance(evidence, str) or too_long):
        raise PartialReportError(f"line {number}: evidence must be a string of at most 4 KiB")
    if isinstance(raw.get("value"), dict | list):
        raise PartialReportError(f"line {number}: value must be a string, a number, a boolean or null")
    observed_at = raw.get("observed_at")
    if observed_at is not None:
        observed_at = _utc(observed_at)
        if observed_at is None:
            raise PartialReportError(f"line {number}: observed_at must be an RFC 3339 timestamp")
    return Observation(
        layer=raw["layer"],
        check=raw["check"],
        scope=raw["scope"],
        status=raw["status"],
        value=raw.get("value"),
        evidence=evidence,
        severity=severity,
        observed_at=observed_at,
    )


def parse_report(text: str) -> list[Observation]:
    """Parse a whole report; raises :class:`PartialReportError` unless it is complete and valid."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise PartialReportError("empty report: no `_end` line")
    observations: list[Observation] = []
    for number, line in enumerate(lines[:-1], start=1):
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as error:
            raise PartialReportError(f"line {number}: not JSON ({error.msg})") from error
        observations.append(_observation(raw, number))
    try:
        end = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise PartialReportError("last line: not JSON, the report is truncated") from error
    if not isinstance(end, dict) or end.get("_end") is not True:
        raise PartialReportError('last line is not `{"_end": true, "lines": N}`: the report is truncated')
    if end.get("lines") != len(observations):
        announced = end.get("lines")
        raise PartialReportError(f"`_end` announces {announced} line(s), the report has {len(observations)}")
    pairs: dict[str, tuple[str, str]] = {}
    for observation in observations:
        mine = (observation.layer, observation.check)
        pair = pairs.setdefault(observation.key, mine)
        if pair != mine:
            raise PartialReportError(f"key {observation.key!r} is produced by {pair} and by {mine}")
    return observations


@dataclass(slots=True)
class SyncPlan:
    """Writes to apply to the ``finding`` objects; empty when the report changes nothing."""

    create: dict[str, dict[str, Any]] = field(default_factory=dict)
    update: dict[str, dict[str, Any]] = field(default_factory=dict)
    unchanged: int = 0

    @property
    def empty(self) -> bool:
        return not self.create and not self.update

    def summary(self) -> dict[str, int]:
        resolved = sum(1 for p in self.update.values() if p.get("status") == "resolved")
        return {
            "created": len(self.create),
            "updated": len(self.update) - resolved,
            "resolved": resolved,
            "unchanged": self.unchanged,
        }


def _max_time(*values: str | None) -> str | None:
    present = [v for v in values if v]
    return max(present) if present else None


def _worst(*severities: str | None) -> str | None:
    present = [s for s in severities if s]
    return max(present, key=SEVERITIES.index) if present else None


def plan_sync(current: dict[str, dict[str, Any]], observations: list[Observation], *, now: str) -> SyncPlan:
    """The plan that brings ``current`` findings (key → properties) in line with a complete report.

    ``now`` is a UTC ISO timestamp (``datetime.isoformat()``), used only when a line has no
    ``observed_at``: a replay of a report that dates its lines changes nothing. A scope reported
    both ``finding`` and ``ok`` in the same report stays open: a contradiction never resolves.
    """
    by_key: dict[str, list[Observation]] = {}
    for observation in observations:
        by_key.setdefault(observation.key, []).append(observation)
    plan = SyncPlan()
    for key, group in sorted(by_key.items()):
        findings = [o for o in group if o.status == "finding"]
        oks = [o for o in group if o.status == "ok"]
        before = current.get(key)
        if before is None and not findings:
            continue  # nothing to open, nothing to resolve
        was_open = before is not None and before.get("status") == "open"
        previously = set(before.get("scopes") or []) if was_open and before else set()
        resolving = [o for o in oks if o.scope in previously]
        open_scopes = (previously - {o.scope for o in resolving}) | {o.scope for o in findings}
        after: dict[str, Any] = dict(before or {})
        after.update({"key": key, "source": group[0].layer, "layer": group[0].layer, "check": group[0].check})
        if open_scopes:
            after["status"] = "open"
            after["resolved_at"] = None
            after["scopes"] = sorted(open_scopes)
            if findings:
                after["severity"] = _worst(*(o.severity for o in findings), after.get("severity"))
                if findings[-1].evidence is not None:
                    after["evidence"] = findings[-1].evidence
                if not was_open or "first_seen" not in after:
                    after["first_seen"] = min((o.observed_at for o in findings if o.observed_at), default=now)
        elif was_open:
            after["scopes"] = []
            after["status"] = "resolved"
            after["resolved_at"] = _max_time(*(o.observed_at for o in resolving)) or now
        last_seen = _max_time(*(o.observed_at for o in findings), after.get("last_seen"))
        if last_seen:
            after["last_seen"] = last_seen
        if before is None:
            plan.create[key] = after
        elif after != before:
            plan.update[key] = after
        else:
            plan.unchanged += 1
    return plan
