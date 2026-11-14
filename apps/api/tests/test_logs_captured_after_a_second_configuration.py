"""A cached logger stays within reach of `structlog.testing.capture_logs` (S65-32, #1717).

`test_run_closed_when_the_wait_ends.py[cancel fails]` captured nothing while its line was printed to
stdout as JSON. Nothing reconfigured structlog during the capture: a logger cached at first use
holds the processors list it was created with, `capture_logs` empties and fills the *current* list
in place, and `configure_logging`, called again by any later lifespan, brought a new list. Every
logger cached under the first configuration was left on the old chain, for good, in that process.
"""

from __future__ import annotations

import io
from collections.abc import Callable
from contextlib import AbstractContextManager

import structlog


def test_a_logger_cached_before_a_second_configuration_is_still_captured() -> None:
    from choregos_api.logging import configure_logging

    logger = structlog.get_logger("choregos.cached")
    configure_logging()  # a first lifespan
    logger.info("first.use")  # cached here, with the chain of that configuration
    configure_logging()  # a second lifespan, in the same process

    with structlog.testing.capture_logs() as logs:
        logger.warning("run.cancel_failed", run_id="r-1")

    assert [(e["event"], e["log_level"], e["run_id"]) for e in logs] == [
        ("run.cancel_failed", "warning", "r-1")
    ], "the second configuration left the cached logger on a chain the capture does not reach"


def test_configuring_again_keeps_the_processors_list() -> None:
    from choregos_api.logging import configure_logging

    configure_logging()
    processors = structlog.get_config()["processors"]
    configure_logging("DEBUG", json_logs=False)

    assert structlog.get_config()["processors"] is processors
    assert isinstance(processors[-1], structlog.dev.ConsoleRenderer), "the new chain is in it"


def test_what_a_test_does_to_structlog_is_put_back_for_the_next_one(
    structlog_configuration_kept: Callable[[], AbstractContextManager[None]],
) -> None:
    """Every test runs in this context manager (the root conftest): a test that swapped the list or
    pointed the writer at its own buffer leaves the next test's captures whole."""
    structlog.configure(cache_logger_on_first_use=True)
    before = structlog.get_config()
    contents = list(before["processors"])
    logger = structlog.get_logger("choregos.earlier")
    logger.info("first.use")  # cached on the list as it is now

    with structlog_configuration_kept():
        structlog.reset_defaults()  # a new list
        structlog.configure(
            processors=[structlog.processors.JSONRenderer()],  # another one
            logger_factory=structlog.PrintLoggerFactory(file=io.StringIO()),
            cache_logger_on_first_use=False,
        )

    after = structlog.get_config()
    assert after["processors"] is before["processors"] and after["processors"] == contents
    assert {k: v for k, v in after.items() if k != "processors"} == {
        k: v for k, v in before.items() if k != "processors"
    }
    with structlog.testing.capture_logs() as logs:
        logger.warning("still.captured")
    assert [e["event"] for e in logs] == ["still.captured"]
