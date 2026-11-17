"""S80-07a: what a worker takes on at once is a setting, and the setting reaches the `Worker`.

`max_concurrent_activities=20` was a literal in `run_worker`, and the other slots were the SDK's
silent defaults: an operator could not raise or lower the ~40 simultaneous runs of a default install
without rebuilding the image.
"""

from __future__ import annotations

from typing import Any

import pytest


class _Stop(Exception):  # noqa: N818 - the test's stop signal
    pass


@pytest.fixture
def _fresh_settings() -> Any:
    from choregos_orchestrator.config import reset_settings_cache

    reset_settings_cache()
    yield
    reset_settings_cache()


async def _worker_keywords(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Run `run_worker` up to the `Worker` constructors and return what each received."""
    import choregos_adapters
    import temporalio.worker
    from choregos_orchestrator import worker
    from temporalio.client import Client

    received: list[dict[str, Any]] = []

    async def connect(*_: object, **__: object) -> object:
        return object()

    class _Worker:
        def __init__(self, client: object, **keywords: Any) -> None:
            received.append(keywords)

        async def __aenter__(self) -> None:
            raise _Stop

        async def __aexit__(self, *_: object) -> None:
            return None

    monkeypatch.setattr(choregos_adapters, "load_extensions", list)
    monkeypatch.setattr(Client, "connect", connect)
    monkeypatch.setattr(temporalio.worker, "Worker", _Worker)
    with pytest.raises(_Stop):
        await worker.run_worker(["orchestrator", "executor"])
    return received


@pytest.mark.usefixtures("_fresh_settings")
async def test_the_defaults_are_the_values_the_worker_always_had(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CHOREGOS_WORKER_MAX_CONCURRENT_ACTIVITIES",
        "CHOREGOS_WORKER_MAX_CONCURRENT_WORKFLOW_TASKS",
        "CHOREGOS_WORKER_MAX_CONCURRENT_LOCAL_ACTIVITIES",
        "CHOREGOS_WORKER_MAX_CACHED_WORKFLOWS",
    ):
        monkeypatch.delenv(name, raising=False)
    received = await _worker_keywords(monkeypatch)
    assert [k["task_queue"] for k in received] == ["orchestrator", "executor"]
    for keywords in received:
        assert keywords["max_concurrent_activities"] == 20
        assert keywords["max_concurrent_workflow_tasks"] == 100
        assert keywords["max_concurrent_local_activities"] == 100
        assert keywords["max_cached_workflows"] == 1000


@pytest.mark.usefixtures("_fresh_settings")
async def test_the_environment_reaches_every_worker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_WORKER_MAX_CONCURRENT_ACTIVITIES", "7")
    monkeypatch.setenv("CHOREGOS_WORKER_MAX_CONCURRENT_WORKFLOW_TASKS", "11")
    monkeypatch.setenv("CHOREGOS_WORKER_MAX_CONCURRENT_LOCAL_ACTIVITIES", "13")
    monkeypatch.setenv("CHOREGOS_WORKER_MAX_CACHED_WORKFLOWS", "0")
    received = await _worker_keywords(monkeypatch)
    assert len(received) == 2
    for keywords in received:
        assert keywords["max_concurrent_activities"] == 7
        assert keywords["max_concurrent_workflow_tasks"] == 11
        assert keywords["max_concurrent_local_activities"] == 13
        assert keywords["max_cached_workflows"] == 0


@pytest.mark.usefixtures("_fresh_settings")
def test_a_worker_with_no_slot_is_refused_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_orchestrator.config import get_settings
    from pydantic import ValidationError

    monkeypatch.setenv("CHOREGOS_WORKER_MAX_CONCURRENT_ACTIVITIES", "0")
    with pytest.raises(ValidationError, match="worker_max_concurrent_activities"):
        get_settings()
