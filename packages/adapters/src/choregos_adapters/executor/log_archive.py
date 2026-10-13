# SPDX-License-Identifier: Apache-2.0
"""The tail of a run's pod log and how the pod ended, read through the `Executor` protocol only.

A failure before the ACP journal starts — an image that does not pull, a CLI that crashes, an
OOM kill — leaves nothing in the journal, and the pod that knew is deleted with its Job. The
orchestrator therefore archives, at the end of every run, the last lines of the runner's log and
the termination reason (S36-02). Nothing here knows one executor from another: any executor that
implements `status` and `logs` (Tekton, Kubernetes Job, local Docker, ACA, the fake, or a new
sandbox executor) gets its runs archived unchanged.

Every function here swallows the executor's errors: archiving is a duty, never a condition — a
run must end even when its logs cannot be read, and the archive then says why.
"""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from choregos_core.domain import ExecRef, ExecStatus, RunLogArchive
from choregos_core.redaction import redact

#: How many of the last log lines are kept.
TAIL_LINES = 200
#: A line longer than this is cut: one minified bundle printed by an agent must not fill the archive.
MAX_LINE_CHARS = 2000
#: How long reading the logs may take before the archive gives up and says so.
LOGS_TIMEOUT_S = 30.0


@dataclass(slots=True)
class LogTail:
    """The last lines of a log, redacted; `error` says why there are none when reading failed."""

    lines: list[str] = field(default_factory=list)
    truncated: bool = False
    error: str | None = None


def _clean(line: str) -> str:
    line = line.rstrip("\r\n")
    if len(line) > MAX_LINE_CHARS:
        line = line[:MAX_LINE_CHARS] + " […]"
    return redact(line)


async def tail_logs(
    executor: Any, ref: ExecRef, *, lines: int = TAIL_LINES, timeout_s: float = LOGS_TIMEOUT_S
) -> LogTail:
    """The last `lines` lines of the run's log, each one redacted. Never raises."""
    kept: deque[str] = deque(maxlen=max(lines, 1))
    seen = 0

    async def read() -> None:
        nonlocal seen
        async for line in executor.logs(ref):
            kept.append(str(line))
            seen += 1

    error: str | None = None
    try:
        if not callable(getattr(executor, "logs", None)):
            raise NotImplementedError(f"{type(executor).__name__} has no logs()")
        await asyncio.wait_for(read(), timeout=timeout_s)
    except TimeoutError:
        error = f"logs not fully read within {timeout_s:g} s"
    except Exception as exc:  # an executor that cannot read its logs must not stop the run
        error = redact(f"logs unavailable: {type(exc).__name__}: {exc}")[:500]
    return LogTail(lines=[_clean(line) for line in kept], truncated=seen > len(kept), error=error)


async def archive_run_logs(
    executor: Any,
    ref: ExecRef,
    status: ExecStatus | None = None,
    *,
    lines: int = TAIL_LINES,
    final: bool = True,
) -> RunLogArchive:
    """How the run ended and its last log lines, redacted. Asks `status` when not given. Never raises."""
    if status is None:
        try:
            status = await executor.status(ref)
        except Exception as exc:
            status = ExecStatus(state="unknown", message=f"status unavailable: {type(exc).__name__}: {exc}")
    tail = await tail_logs(executor, ref, lines=lines)
    return RunLogArchive(
        state=status.state,
        exit_code=status.exit_code,
        reason=status.reason,
        message=redact(status.message)[:700],
        lines=tail.lines,
        truncated=tail.truncated,
        error=tail.error,
        final=final,
    )


__all__ = ["LOGS_TIMEOUT_S", "MAX_LINE_CHARS", "TAIL_LINES", "LogTail", "archive_run_logs", "tail_logs"]
