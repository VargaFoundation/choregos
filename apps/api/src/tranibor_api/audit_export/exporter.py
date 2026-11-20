# SPDX-License-Identifier: Apache-2.0
"""The loop that ships the audit chains to a SIEM (ADR 0196).

For each chain (every organisation's, then the platform's) and one sink, `audit_export_cursors` holds the
`seq` and `hash` of the last entry the sink accepted. A pass, per chain:

1. lock the cursor's row (`FOR UPDATE SKIP LOCKED`): another replica that is already shipping this chain
   makes this one skip it, so the replicas need no election and an entry is shipped by one of them at a time;
2. read the next batch after `seq`, in order;
3. `sink.send(batch)`: raises `SinkError` unless the whole batch was accepted;
4. move the cursor and commit.

A crash or an error before 4 leaves the cursor where it was, and the batch goes again: **at least once**,
**in order** (a chain's entries are committed in the order of `seq`: the trigger holds the chain's head
until the writer's transaction ends, so no `seq` appears after a greater one), and **never ahead** of
the sink. A receiver told the chain's place of each event sees a repeat by its `seq`, and can verify the
chain (`ExportEvent`). The loop runs in every API replica, as the other periodic jobs do.

What it does not do: retention (a cursor that points before a deleted prefix is shipped what remains, and
the gap is logged), a second sink, or verification of the chain before shipping: the log is shipped as it
stands, a break is `choregos-admin audit verify`'s to find, and the receiver's.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

from prometheus_client import Counter as PromCounter
from sqlalchemy import select
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.ext.asyncio import AsyncSession
from tranibor_core import utcnow

from ..audit_chain import GENESIS, Link, chains, links_after
from ..config import Settings
from ..db.models import AuditExportCursor
from ..db.session import ALL_ORGS, session_scope
from ..logging import get_logger
from ..metrics import registry
from .events import ExportEvent
from .sinks import FakeSink, HttpJsonSink, Sink, SinkError, SyslogSink

log = get_logger("choregos.audit.export")

events_total = PromCounter(
    "choregos_audit_export_events_total",
    "Audit entries a SIEM sink accepted (this process)",
    ["sink"],
    registry=registry,
)
failures_total = PromCounter(
    "choregos_audit_export_failures_total",
    "Batches a SIEM sink refused or could not be given (this process)",
    ["sink"],
    registry=registry,
)
#: The longest wait between two passes after consecutive failures.
MAX_BACKOFF_S = 300.0


@dataclass
class PassResult:
    shipped: int = 0
    failed: int = 0
    skipped: int = 0


def build_sink(settings: Settings) -> Sink:
    """The sink the settings describe. `ValueError` says what is wrong: the API refuses to start on it."""
    kind = settings.audit_export_sink
    if kind == "fake":
        if not settings.fakes:
            raise ValueError("audit export: the `fake` sink is for TRANIBOR_FAKES=1 only")
        return FakeSink()
    if kind == "syslog":
        return SyslogSink(
            settings.audit_export_syslog_host,
            settings.audit_export_syslog_port,
            format=settings.audit_export_format or "rfc5424",
            ca_file=settings.audit_export_ca_file,
        )
    return HttpJsonSink(
        settings.audit_export_url,
        format=settings.audit_export_format or "webhook",
        token=settings.audit_export_token.get_secret_value(),
        header=settings.audit_export_header,
        scheme=settings.audit_export_scheme,
        index=settings.audit_export_index,
        ca_file=settings.audit_export_ca_file,
        source=settings.audit_export_source,
    )


class Exporter:
    def __init__(
        self,
        sink: Sink,
        *,
        name: str = "siem",
        orgs: list[str] | None = None,
        platform: bool = True,
        batch: int = 500,
        source: str = "choregos",
        with_canonical: bool = True,
        scope: Callable[..., AbstractAsyncContextManager[AsyncSession]] = session_scope,
    ) -> None:
        self.sink = sink
        self.name = name
        self.orgs = orgs or []
        self.platform = platform
        self.batch = max(1, batch)
        self.source = source
        self.with_canonical = with_canonical
        self._scope = scope

    @classmethod
    def from_settings(cls, settings: Settings, sink: Sink | None = None) -> Exporter:
        return cls(
            sink or build_sink(settings),
            name=settings.audit_export_name,
            orgs=settings.audit_export_orgs,
            platform=settings.audit_export_platform,
            batch=settings.audit_export_batch,
            source=settings.audit_export_source,
            with_canonical=settings.audit_export_canonical,
        )

    def _event(self, link: Link, label: str) -> ExportEvent:
        # `links_after` returns only chained entries: the three columns are there.
        assert link.seq is not None and link.prev_hash is not None and link.hash is not None
        return ExportEvent(
            link=link,
            seq=link.seq,
            prev_hash=link.prev_hash,
            hash=link.hash,
            org=label,
            source=self.source,
            with_canonical=self.with_canonical,
        )

    async def _ensure_cursor(self, chain: str) -> None:
        """The chain's cursor, created at entry 0 if it is not there. Two replicas racing is fine."""
        async with self._scope(orgs=ALL_ORGS) as session:
            dialect = session.get_bind().dialect.name
            insert = (postgresql if dialect == "postgresql" else sqlite).insert
            await session.execute(
                insert(AuditExportCursor)
                .values(
                    sink=self.name,
                    chain=chain,
                    org_id=chain or None,
                    seq=0,
                    hash=GENESIS,
                    updated_at=utcnow(),
                )
                .on_conflict_do_nothing()
            )

    async def ship_chain(self, chain: str, label: str) -> int | None:
        """Ship what the sink has not got of one chain; the number of entries accepted, or `None` when
        another replica holds the chain. `SinkError` when the sink refused a batch: what came before it
        stays shipped, the cursor stands after it."""
        await self._ensure_cursor(chain)
        shipped = 0
        while True:
            async with self._scope(orgs=ALL_ORGS) as session:
                cursor = await self._lock(session, chain)
                if cursor is None:
                    return shipped if shipped else None
                links = await links_after(session, chain, cursor.seq, self.batch)
                if not links:
                    return shipped
                self._note_gap(cursor, links[0], label)
                await self.sink.send([self._event(link, label) for link in links])
                last = links[-1]
                cursor.seq = int(last.seq or 0)
                cursor.hash = str(last.hash)
                cursor.updated_at = utcnow()
            shipped += len(links)
            events_total.labels(self.sink.kind).inc(len(links))
            if len(links) < self.batch:
                return shipped

    async def _lock(self, session: AsyncSession, chain: str) -> AuditExportCursor | None:
        return (
            await session.execute(
                select(AuditExportCursor)
                .where(AuditExportCursor.sink == self.name, AuditExportCursor.chain == chain)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()

    def _note_gap(self, cursor: AuditExportCursor, first: Link, label: str) -> None:
        """The entry after the cursor does not follow the one the cursor stands on: a prefix was removed, or
        the chain was rewritten. It is shipped all the same, and the receiver, which has the `prev_hash`,
        sees it; this is the platform saying it saw it."""
        if first.seq == cursor.seq + 1 and first.prev_hash == cursor.hash:
            return
        log.warning(
            "audit.export.chain_discontinuity",
            sink=self.name,
            org=label,
            cursor_seq=cursor.seq,
            next_seq=first.seq,
            linked=first.prev_hash == cursor.hash,
        )

    async def run_pass(self) -> PassResult:
        """Every chain, once: each is shipped until the sink has all of it. A chain whose delivery fails does
        not stop the others."""
        result = PassResult()
        async with self._scope(orgs=ALL_ORGS) as session:
            targets = await chains(session, self.orgs, self.platform)
        for chain, label in targets:
            try:
                shipped = await self.ship_chain(chain, label)
            except SinkError as error:
                result.failed += 1
                failures_total.labels(self.sink.kind).inc()
                log.warning("audit.export.failed", sink=self.name, org=label, error=str(error)[:300])
                continue
            if shipped is None:
                result.skipped += 1
            else:
                result.shipped += shipped
        if result.shipped:
            log.info("audit.export.shipped", sink=self.name, entries=result.shipped)
        return result


async def export_loop(settings: Settings, exporter: Exporter | None = None) -> None:
    """Background task of the API (every replica). A failed pass does not stop the API; consecutive failures
    stretch the wait (twice each time, to `MAX_BACKOFF_S`) so a receiver that is down is not hammered."""
    exporter = exporter or Exporter.from_settings(settings)
    interval = max(1.0, settings.audit_export_interval_s)
    failures = 0
    log.info(
        "audit.export.started",
        sink=exporter.name,
        kind=exporter.sink.kind,
        organisations=exporter.orgs or "all",
        platform=exporter.platform,
    )
    try:
        while True:
            try:
                result = await exporter.run_pass()
                failures = failures + 1 if result.failed else 0
            except asyncio.CancelledError:
                raise
            except Exception as error:  # including a slug of the filter that is no organisation (yet)
                failures += 1
                log.warning("audit.export.pass_failed", sink=exporter.name, error=str(error)[:200])
            wait = min(MAX_BACKOFF_S, interval * 2 ** min(failures, 8)) if failures else interval
            await asyncio.sleep(wait)
    finally:
        await exporter.sink.close()
