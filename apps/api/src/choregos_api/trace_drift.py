# SPDX-License-Identifier: Apache-2.0
"""The nightly comparison of the cost ledger with the runs' traces (ADR 0018 point 3, S40-20c).

    python -m choregos_api.trace_drift                 # runs ended in the last 24 hours
    python -m choregos_api.trace_drift --hours 72 --settle-minutes 0

The ledger is the truth for money (ADR 0004); a trace carries `gen_ai.usage.*` for reading. For
each run whose spend was collected in the window, the job sums the ledger's model rows and the
usage on the gateway's spans of the run's trace (read from Tempo by the trace id derived from the
run id), and compares input and output tokens.

- **matched**: both agree within the tolerance (`CHOREGOS_TRACE_DRIFT_TOLERANCE`, a share of the
  ledger's figure, at least one token);
- **drift**: they do not. The drift is recorded once per run as a platform audit line
  (`trace.drift`, no organisation: only a platform session reads it) and logged; the ledger is
  never corrected;
- **missing**: the ledger counts tokens and the trace has no gateway usage, or no trace at all. A
  trace can be sampled, dropped or late (ADR 0018, Conditions): counted, not recorded.

Only the spans whose resource is the gateway (`choregos-gateway`) count: a model call is the
gateway's span, and a span another process wrote cannot add tokens to the comparison.

The job exits 1 when Tempo answers with an error for any run (the CronJob then shows failed), 2
when no Tempo address is set, and 0 otherwise, drifts included: a drift is a finding, not a failure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal, Protocol

import httpx
from choregos_core import utcnow
from choregos_core.llm_spans import SERVICE_NAME as GATEWAY_SERVICE
from choregos_core.telemetry import USAGE_INPUT_TOKENS, USAGE_OUTPUT_TOKENS, run_trace_ids
from sqlalchemy import func, select

from .audit import record
from .config import get_settings
from .db.models import AuditLog, CostLedger, Project, Run, WorkItem
from .db.session import session_scope
from .logging import configure_logging, get_logger

log = get_logger(__name__)

#: The audit action a drift is recorded under, once per run.
DRIFT_ACTION = "trace.drift"

Verdict = Literal["matched", "drift", "missing"]


class TempoError(RuntimeError):
    """Tempo answered, but not with a trace nor a 404."""


class TraceReader(Protocol):
    async def trace(self, trace_id: str) -> dict[str, Any] | None: ...


class TempoReader:
    """`GET /api/traces/<id>` on Tempo's query port: the trace, or `None` when Tempo has none."""

    def __init__(
        self, base_url: str, *, timeout_s: float = 15.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout_s, transport=transport
        )

    async def trace(self, trace_id: str) -> dict[str, Any] | None:
        try:
            answer = await self._client.get(f"/api/traces/{trace_id}")
        except httpx.HTTPError as exc:
            raise TempoError(f"Tempo unreachable: {exc}") from exc
        if answer.status_code == 404:
            return None
        if answer.status_code != 200:
            raise TempoError(f"Tempo answered {answer.status_code}: {answer.text[:200]}")
        body: dict[str, Any] = answer.json()
        return body

    async def aclose(self) -> None:
        await self._client.aclose()


@dataclass(frozen=True)
class Usage:
    tokens_in: int = 0
    tokens_out: int = 0
    spans: int = 0


def _attributes(holder: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for item in holder.get("attributes", []) or []:
        value = item.get("value") or {}
        out[item.get("key", "")] = next(iter(value.values()), None) if value else None
    return out


def _count(value: Any) -> int:
    # OTLP/JSON writes a 64-bit integer as a string.
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def gateway_usage(body: dict[str, Any]) -> Usage:
    """The tokens on the gateway's spans of a Tempo trace (`batches` or OTLP `resourceSpans`)."""
    batches = (
        body.get("batches") or body.get("resourceSpans") or (body.get("trace") or {}).get("resourceSpans")
    )
    tokens_in = tokens_out = spans = 0
    for batch in batches or []:
        if _attributes(batch.get("resource") or {}).get("service.name") != GATEWAY_SERVICE:
            continue
        for scope in batch.get("scopeSpans", batch.get("instrumentationLibrarySpans", [])) or []:
            for span in scope.get("spans", []) or []:
                attributes = _attributes(span)
                if USAGE_INPUT_TOKENS not in attributes and USAGE_OUTPUT_TOKENS not in attributes:
                    continue
                tokens_in += _count(attributes.get(USAGE_INPUT_TOKENS))
                tokens_out += _count(attributes.get(USAGE_OUTPUT_TOKENS))
                spans += 1
    return Usage(tokens_in, tokens_out, spans)


def _apart(ledger: int, traced: int, tolerance: float) -> bool:
    return abs(ledger - traced) > max(1.0, tolerance * ledger)


def verdict(ledger: Usage, traced: Usage | None, tolerance: float) -> Verdict:
    if traced is None or traced.spans == 0:
        return "matched" if ledger.tokens_in == ledger.tokens_out == 0 else "missing"
    apart = _apart(ledger.tokens_in, traced.tokens_in, tolerance) or _apart(
        ledger.tokens_out, traced.tokens_out, tolerance
    )
    return "drift" if apart else "matched"


@dataclass
class Report:
    since: str
    until: str
    runs: int = 0
    matched: int = 0
    drift: int = 0
    missing: int = 0
    recorded: int = 0
    errors: list[str] = field(default_factory=list)


async def _runs_in_window(since: datetime, until: datetime) -> list[tuple[Run, Usage, str, str]]:
    """The model-spend runs collected in the window, with their ledger sums, project and key."""
    async with session_scope(orgs="*") as session:
        ledger = (
            select(
                CostLedger.run_id,
                func.coalesce(func.sum(CostLedger.tokens_in), 0),
                func.coalesce(func.sum(CostLedger.tokens_out), 0),
            )
            .where(CostLedger.kind == "model", CostLedger.run_id.is_not(None))
            .group_by(CostLedger.run_id)
            .subquery()
        )
        rows = (
            await session.execute(
                select(Run, ledger.c[1], ledger.c[2], Project.slug, WorkItem.tracker_key)
                .join(ledger, ledger.c.run_id == Run.id)
                .join(Project, Project.id == Run.project_id)
                .join(WorkItem, WorkItem.id == Run.work_item_id)
                .where(
                    Run.spend_collected.is_(True),
                    Run.ended_at.is_not(None),
                    Run.ended_at >= since,
                    Run.ended_at < until,
                )
                .order_by(Run.ended_at)
            )
        ).all()
        return [(run, Usage(int(i), int(o)), slug, key) for run, i, o, slug, key in rows]


async def _record_drift(run: Run, project: str, work_item: str, ledger: Usage, traced: Usage) -> bool:
    """One audit line per run, whatever the number of nights that see it (ADR 0008)."""
    async with session_scope(orgs="*") as session:
        already = (
            await session.execute(
                select(AuditLog.id).where(AuditLog.action == DRIFT_ACTION, AuditLog.target_id == run.id[:64])
            )
        ).first()
        if already is not None:
            return False
        await record(
            session,
            None,
            DRIFT_ACTION,
            org_id=None,
            target_type="run",
            target_id=run.id[:64],
            run_id=run.id,
            project=project,
            work_item=work_item,
            stage=run.stage_role,
            ledger={"tokens_in": ledger.tokens_in, "tokens_out": ledger.tokens_out},
            trace=asdict(traced),
        )
        return True


async def compare(
    reader: TraceReader,
    *,
    since: datetime,
    until: datetime,
    tolerance: float,
) -> Report:
    report = Report(since=since.isoformat(), until=until.isoformat())
    for run, ledger, project, work_item in await _runs_in_window(since, until):
        report.runs += 1
        keys = {"run_id": run.id, "project": project, "work_item": work_item, "stage": run.stage_role}
        trace_id, _ = run_trace_ids(run.id)
        try:
            body = await reader.trace(f"{trace_id:032x}")
        except TempoError as exc:
            report.errors.append(f"{run.id}: {exc}")
            log.error("trace not read", error=str(exc)[:200], **keys)
            continue
        traced = gateway_usage(body) if body is not None else None
        outcome = verdict(ledger, traced, tolerance)
        setattr(report, outcome, getattr(report, outcome) + 1)
        if outcome == "drift" and traced is not None:
            log.warning(
                "the trace disagrees with the ledger",
                ledger_in=ledger.tokens_in,
                ledger_out=ledger.tokens_out,
                trace_in=traced.tokens_in,
                trace_out=traced.tokens_out,
                **keys,
            )
            if await _record_drift(run, project, work_item, ledger, traced):
                report.recorded += 1
    return report


async def _main(hours: float, settle_minutes: float) -> int:
    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)
    if not settings.tempo_url:
        print("trace_drift: CHOREGOS_TEMPO_URL is not set", file=sys.stderr)
        return 2
    until = utcnow() - timedelta(minutes=settle_minutes)
    reader = TempoReader(settings.tempo_url)
    try:
        report = await compare(
            reader,
            since=until - timedelta(hours=hours),
            until=until,
            tolerance=settings.trace_drift_tolerance,
        )
    finally:
        await reader.aclose()
    print(json.dumps(asdict(report), sort_keys=True))
    return 1 if report.errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m choregos_api.trace_drift", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--hours", type=float, default=24.0, help="the window of run ends to read")
    parser.add_argument(
        "--settle-minutes",
        type=float,
        default=30.0,
        help="leave out runs ended this recently: spans arrive late",
    )
    args = parser.parse_args(argv)
    return asyncio.run(_main(args.hours, args.settle_minutes))


if __name__ == "__main__":
    sys.exit(main())
