# SPDX-License-Identifier: Apache-2.0
"""`python -m choregos_api.egress`: start the proxy and its health port."""

from __future__ import annotations

import asyncio
import contextlib
import signal

import structlog

from .config import EgressSettings
from .proxy import EgressProxy, serve_health
from .report import HttpDecisionSink, Reporter
from .sources import HttpRunNetworkSource

log = structlog.get_logger("choregos.egress")


async def main() -> None:
    settings = EgressSettings()
    source = HttpRunNetworkSource(settings.api_url, cache_seconds=settings.cache_seconds)
    sink = HttpDecisionSink(settings.api_url)
    reporter = Reporter(sink)
    proxy = EgressProxy(settings, source, reporter)
    port = await proxy.start()
    health = await serve_health(settings.health_port)
    log.info("choregos-egress started", port=port, health_port=settings.health_port, ports=settings.ports)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    async def flusher() -> None:
        while True:
            await asyncio.sleep(settings.flush_seconds)
            await reporter.flush()
            proxy.limiter.forget_idle()

    task = asyncio.create_task(flusher())
    await stop.wait()
    task.cancel()
    await proxy.close()
    health.close()
    await reporter.flush()
    await source.aclose()
    await sink.aclose()


if __name__ == "__main__":
    asyncio.run(main())
