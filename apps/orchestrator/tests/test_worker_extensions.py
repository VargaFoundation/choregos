"""The worker loads the extensions (S20-08): their governed-action EFFECTS run on it.

It did not load them. The API knew an extension's effect in order to propose it; the worker, which
had to play it, called it "unknown": the action failed at the very moment it was served.
"""

from __future__ import annotations

import pytest


class _Stop(Exception):  # noqa: N818 - the test's stop signal
    pass


async def test_the_worker_loads_the_extensions_before_joining_temporal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import choregos_adapters
    from choregos_orchestrator import worker
    from temporalio.client import Client

    order: list[str] = []
    monkeypatch.setattr(choregos_adapters, "load_extensions", lambda: order.append("extensions") or [])

    async def join(*_: object, **__: object) -> None:
        order.append("temporal")
        raise _Stop

    monkeypatch.setattr(Client, "connect", join)
    with pytest.raises(_Stop):
        await worker.run_worker(["orchestrator"])
    assert order == ["extensions", "temporal"]
