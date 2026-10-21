# SPDX-License-Identifier: Apache-2.0
"""Conversions run in the background, never inside the request that uploaded the document.

An upload answers 202 with a `pending` document; `schedule()` starts a task that claims it
(`pending` → `converting`, one replica only, by a conditional UPDATE), runs the converter in a child
process with a timeout, and writes `ready` with the markdown, or `failed` with the reason. A replica
that dies mid-conversion leaves a `converting` row: `recover()`, at start-up, puts the stale ones
back to `pending` and schedules every pending one.

Nothing here may hold the API's event loop (S39-26): the converter is a child process, started at a
low priority so that, under a CPU limit shared by the whole pod, the kernel serves the API first;
what stays in the process and is CPU-bound (parsing the answer, cutting chunks) runs in a worker
thread. Only I/O awaits on the loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any

from choregos_core import utcnow
from sqlalchemy import select, update
from sqlalchemy.orm import undefer

from ..config import get_settings
from ..db.models import KnowledgeBase, KnowledgeDocument, Organization
from ..db.session import TOUT, session_scope
from ..logging import get_logger

logger = get_logger("choregos.knowledge")

PENDING, CONVERTING, READY, FAILED = "pending", "converting", "ready", "failed"
_HEADING = re.compile(r"^#{1,6}\s+(.+?)\s*#*\s*$", re.MULTILINE)
#: The environment a converter sees: enough to run Python, nothing of the API's secrets.
_ENV_KEPT = ("PATH", "LANG", "LC_ALL", "HOME", "TMPDIR", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONHOME")


#: Niceness of the converter process: the API's own threads win every contest for the pod's CPU.
CONVERTER_NICE = 15


def _lower_priority() -> None:
    """Runs in the child between fork and exec."""
    with contextlib.suppress(OSError):
        os.nice(CONVERTER_NICE)


class ConversionError(RuntimeError):
    """The converter could not produce markdown; the message is what the console shows."""


def title_of(markdown: str, declared: str | None, filename: str) -> str:
    """The title a document goes by: the one its format declares, its first heading, its name."""
    if declared and declared.strip():
        return declared.strip()[:300]
    first = _HEADING.search(markdown)
    if first:
        return first.group(1).strip()[:300]
    return PurePosixPath(filename).stem[:300] or filename[:300]


def child_environment() -> dict[str, str]:
    """What the converter process sees of the environment: no database URL, no key, no token."""
    return {key: os.environ[key] for key in _ENV_KEPT if key in os.environ}


async def run_converter(
    data: bytes, extension: str, *, timeout_s: float, max_bytes: int, rows_per_sheet: int | None = None
) -> dict[str, Any]:
    """Runs `knowledge.convert` in a child process; raises `ConversionError` on any failure."""
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "choregos_api.knowledge.convert",
        extension,
        # The cap travels by argv: the child's environment is bare by design (`child_environment`).
        *([str(rows_per_sheet)] if rows_per_sheet else []),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_environment(),
        preexec_fn=_lower_priority,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(data), timeout=timeout_s)
    except TimeoutError as exc:
        process.kill()
        await process.wait()
        raise ConversionError(f"conversion took longer than {timeout_s:g} s and was stopped") from exc
    if process.returncode != 0:
        raise ConversionError(
            stderr.decode("utf-8", "replace").strip()[:1000] or f"converter exited {process.returncode}"
        )
    # Up to `max_bytes` of JSON and UTF-8: decoded off the loop.
    return await asyncio.to_thread(_checked_answer, stdout, max_bytes)


def _checked_answer(stdout: bytes, max_bytes: int) -> dict[str, Any]:
    try:
        result = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ConversionError("the converter answered something that is not JSON") from exc
    markdown = str(result.get("markdown") or "")
    if len(markdown.encode("utf-8")) > max_bytes:
        raise ConversionError(
            f"the converted text exceeds {max_bytes // (1024 * 1024)} MiB: split the document"
        )
    if not markdown.strip():
        raise ConversionError("the document holds no text (a scanned PDF needs OCR, not supported)")
    return {"markdown": markdown, "title": result.get("title")}


async def convert_document(org: str, document_id: str) -> str:
    """Claims one pending document, converts it, records the outcome; returns the final status.

    Idempotent: a document another replica already claimed, or already converted, is left alone.
    """
    settings = get_settings()
    async with session_scope(orgs=[org]) as session:
        claimed = await session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id, KnowledgeDocument.status == PENDING)
            .values(status=CONVERTING, error=None, updated_at=utcnow())
        )
        if claimed.rowcount != 1:  # type: ignore[attr-defined]
            return ""
    async with session_scope(orgs=[org]) as session:
        document = (
            await session.execute(
                select(KnowledgeDocument)
                .options(undefer(KnowledgeDocument.content))
                .where(KnowledgeDocument.id == document_id)
            )
        ).scalar_one()
        data, filename = document.content or b"", document.filename
    extension = PurePosixPath(filename.lower()).suffix
    try:
        result = await run_converter(
            data,
            extension,
            timeout_s=settings.knowledge_conversion_timeout_s,
            max_bytes=settings.knowledge_max_markdown_mib * 1024 * 1024,
            rows_per_sheet=settings.knowledge_xlsx_rows_per_sheet,
        )
        status, values = (
            READY,
            {
                "markdown": result["markdown"],
                "title": await asyncio.to_thread(title_of, result["markdown"], result.get("title"), filename),
                "error": None,
                "converted_at": utcnow(),
            },
        )
    except ConversionError as exc:
        status, values = FAILED, {"error": str(exc), "markdown": None}
    async with session_scope(orgs=[org]) as session:
        await session.execute(
            update(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id, KnowledgeDocument.status == CONVERTING)
            .values(status=status, updated_at=utcnow(), **values)
        )
    logger.info("knowledge.document.converted", org=org, document=document_id, status=status)
    if status == READY:
        # An `index` base cuts and embeds the document now; a failure leaves it unindexed, which
        # the base's page shows (indexed documents against ready ones), and a reindex repairs.
        from .index import index_document

        try:
            await index_document(org, document_id)
        except Exception:
            logger.exception("knowledge.document.index_failed", org=org, document=document_id)
    return status


class Conversions:
    """The background tasks of this replica, bounded by `knowledge_conversion_concurrency`."""

    def __init__(self) -> None:
        self._tasks: set[asyncio.Task[None]] = set()
        self._limit: asyncio.Semaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    def _semaphore(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        if self._limit is None or self._loop is not loop:
            self._limit = asyncio.Semaphore(max(1, get_settings().knowledge_conversion_concurrency))
            self._loop = loop
        return self._limit

    def schedule(self, org: str, document_id: str) -> None:
        """Starts the conversion after the caller's transaction is COMMITTED (it claims the row)."""
        task = asyncio.get_running_loop().create_task(self._run(org, document_id))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _run(self, org: str, document_id: str) -> None:
        async with self._semaphore():
            try:
                await convert_document(org, document_id)
            except Exception:  # a task that dies silently would leave `converting` forever
                logger.exception("knowledge.document.conversion_crashed", org=org, document=document_id)

    def spawn(self, label: str, work: Callable[[], Awaitable[Any]]) -> None:
        """Any other background work of the knowledge bases (a reindex), under the same limit."""

        async def run() -> None:
            async with self._semaphore():
                try:
                    await work()
                except Exception:
                    logger.exception("knowledge.background_failed", work=label)

        task = asyncio.get_running_loop().create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Waits for every scheduled conversion (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def stop(self) -> None:
        """Cancels what runs (shutdown): the rows stay `converting`, and `recover()` takes them back."""
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def recover(self) -> int:
        """Re-schedules what a stopped replica left: pending documents, and stale `converting` ones."""
        settings = get_settings()
        stale = utcnow() - timedelta(seconds=settings.knowledge_conversion_timeout_s * 2 + 60)
        async with session_scope(orgs=TOUT) as session:
            await session.execute(
                update(KnowledgeDocument)
                .where(KnowledgeDocument.status == CONVERTING, KnowledgeDocument.updated_at < stale)
                .values(status=PENDING)
            )
            rows = (
                await session.execute(
                    select(Organization.slug, KnowledgeDocument.id)
                    .join(Organization, Organization.id == KnowledgeDocument.org_id)
                    .where(KnowledgeDocument.status == PENDING)
                )
            ).tuples()
            pending = list(rows)
        for org, document_id in pending:
            self.schedule(org, document_id)
        await self._interrupt_distillations()
        return len(pending)

    async def _interrupt_distillations(self) -> int:
        """A distillation `running` is a task of a replica that stopped: say so, so the base can be
        distilled again (the route also ignores one older than 30 minutes, for the replica that died
        without a start-up)."""
        async with session_scope(orgs=TOUT) as session:
            bases = (
                await session.execute(select(KnowledgeBase).where(KnowledgeBase.distillation.is_not(None)))
            ).scalars()
            interrupted = [b for b in bases if (b.distillation or {}).get("status") == "running"]
            for base in interrupted:
                base.distillation = {
                    **(base.distillation or {}),
                    "status": "failed",
                    "error": "interrupted by a restart: distil again",
                    "at": utcnow().isoformat(),
                }
        return len(interrupted)


conversions = Conversions()
