"""The tail of a run's pod log and how the pod ended, through the `Executor` protocol (S36-02)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from choregos_adapters.executor.log_archive import MAX_LINE_CHARS, archive_run_logs, tail_logs
from choregos_adapters.fakes.executor import FakeExecutor
from choregos_contracts import ExecutorKind
from choregos_core.domain import ExecRef, ExecStatus, StageJobSpec

# Assembled at run time: a whole fake token would trip the repository's secret scanner.
TOKEN = "ghs_" + "x1" * 15


def _spec(run_id: str) -> StageJobSpec:
    return StageJobSpec(
        run_id=run_id,
        project_slug="billing-api",
        namespace="choregos-billing-api",
        runner_image="runner:test",
        api_url="http://api",
        run_token="t",
    )


async def test_a_crashing_pod_is_archived_with_its_reason_and_last_lines_token_masked() -> None:
    executor = FakeExecutor()
    executor.crash_next(
        exit_code=137,
        reason="OOMKilled",
        log_lines=[
            "runner: starting",
            f"env: GITHUB_TOKEN={TOKEN}",
            "agent: loading 4 GB of embeddings",
            "Killed",
        ],
    )
    ref = await executor.start(_spec("r-oom"))
    archive = await archive_run_logs(executor, ref)

    assert (archive.state, archive.exit_code, archive.reason) == ("failed", 137, "OOMKilled")
    assert archive.lines[-1] == "Killed"
    assert archive.lines[1] == "env: GITHUB_TOKEN=[REDACTED]"
    assert TOKEN not in archive.model_dump_json()
    assert archive.error is None and archive.truncated is False


async def test_a_runner_that_never_started_says_why() -> None:
    executor = FakeExecutor()
    executor.crash_next(exit_code=None, reason="ImagePullBackOff", message="pull access denied")
    ref = await executor.start(_spec("r-pull"))
    archive = await archive_run_logs(executor, ref)
    assert (archive.exit_code, archive.reason, archive.lines) == (None, "ImagePullBackOff", [])
    assert archive.message == "pull access denied"


async def test_only_the_tail_is_kept_and_long_lines_are_cut() -> None:
    executor = FakeExecutor()
    executor.crash_next(log_lines=[f"line {i}" for i in range(500)] + ["x" * (MAX_LINE_CHARS + 50)])
    ref = await executor.start(_spec("r-long"))
    tail = await tail_logs(executor, ref, lines=10)
    assert tail.truncated is True and len(tail.lines) == 10
    assert tail.lines[0] == "line 491"
    assert len(tail.lines[-1]) < MAX_LINE_CHARS + 10


class _NoLogs:
    """An executor whose logs cannot be read: the archive must still be written, saying why."""

    kind = ExecutorKind.K8S_JOB
    capabilities: frozenset[str] = frozenset()

    async def status(self, ref: ExecRef) -> ExecStatus:
        return ExecStatus(state="failed", exit_code=1, reason="Error", message="Bearer abcdef123456 refused")

    def logs(self, ref: ExecRef) -> AsyncIterator[str]:
        raise RuntimeError("pods/log is forbidden for token=supersecretvalue")


async def test_unreadable_logs_still_give_an_archive_that_says_why() -> None:
    ref = ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-1", namespace="ns", run_id="r-1")
    archive = await archive_run_logs(_NoLogs(), ref)
    assert archive.reason == "Error" and archive.lines == []
    assert archive.error is not None and "forbidden" in archive.error
    assert "supersecretvalue" not in archive.error, "the error message is redacted too"
    assert "abcdef123456" not in archive.message


class _SlowLogs(_NoLogs):
    async def logs(self, ref: ExecRef) -> AsyncIterator[str]:  # type: ignore[override]
        import asyncio

        yield "first line"
        await asyncio.sleep(10)
        yield "never read"


async def test_logs_that_never_end_are_cut_by_the_timeout() -> None:
    ref = ExecRef(kind=ExecutorKind.K8S_JOB, name="run-r-1", namespace="ns", run_id="r-1")
    tail = await tail_logs(_SlowLogs(), ref, timeout_s=0.05)
    assert tail.lines == ["first line"]
    assert tail.error is not None and "not fully read" in tail.error


async def test_status_failing_still_gives_an_archive() -> None:
    class _Broken(FakeExecutor):
        async def status(self, ref: ExecRef) -> Any:
            raise ConnectionError("api server down")

    executor = _Broken()
    ref = ExecRef(kind=ExecutorKind.FAKE, name="fake-r", run_id="r")
    archive = await archive_run_logs(executor, ref)
    assert archive.state == "unknown" and "api server down" in archive.message
    assert archive.lines == executor.log_lines
