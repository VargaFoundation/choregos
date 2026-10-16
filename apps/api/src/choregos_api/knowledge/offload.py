# SPDX-License-Identifier: Apache-2.0
"""CPU-bound work of the knowledge bases runs in a child process, never on the API's event loop.

A worker thread is not enough: pure-Python work (cutting a long document, tokenising, vector maths)
holds the GIL and still slows every request of the process (S39-26: a 1.5 s spin in a thread made a
concurrent GET wait 0.7 s). One spawned worker, niced so that under the pod's CPU limit the API's
own threads win, bounded by a timeout; a worker that dies or hangs is replaced.
"""

from __future__ import annotations

import asyncio
import contextlib
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from ..logging import get_logger

logger = get_logger("choregos.knowledge.offload")

#: Niceness of the worker; see `conversion.CONVERTER_NICE`.
NICE = 15
#: What a single job may take before the worker is replaced and the job fails.
TIMEOUT_S = 120.0

_pool: ProcessPoolExecutor | None = None


def _lower_priority() -> None:
    with contextlib.suppress(OSError):
        os.nice(NICE)


def _executor() -> ProcessPoolExecutor:
    global _pool  # noqa: PLW0603 - one worker pool per process, like the object store
    if _pool is None:
        _pool = ProcessPoolExecutor(
            max_workers=1, mp_context=multiprocessing.get_context("spawn"), initializer=_lower_priority
        )
    return _pool


def shutdown() -> None:
    """Drops the worker (shutdown, tests); the next job starts a new one."""
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        for process in list(getattr(pool, "_processes", {}).values()):
            with contextlib.suppress(Exception):
                process.kill()
        pool.shutdown(wait=False, cancel_futures=True)


async def run(fn: Any, *args: Any, timeout_s: float | None = None) -> Any:
    """`fn(*args)` in the worker: `fn` and its arguments must be picklable (module level)."""
    loop = asyncio.get_running_loop()
    future = loop.run_in_executor(_executor(), fn, *args)
    try:
        return await asyncio.wait_for(future, timeout=timeout_s or TIMEOUT_S)
    except TimeoutError:
        logger.warning("knowledge.offload.timeout", job=getattr(fn, "__name__", "?"))
        shutdown()
        raise
    except Exception as exc:
        if type(exc).__name__ == "BrokenProcessPool":
            shutdown()
        raise
