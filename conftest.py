"""Root conftest: one PostgreSQL database per pytest-xdist worker.

The suites that run on PostgreSQL (`TRANIBOR_TEST_DATABASE_URL`) reset the schema of the database
they are given, so two workers cannot share one. Each worker takes its own, `<name>_gw0`,
`<name>_gw1`, ... created beside the configured one, and the variable is rewritten before any
test module reads it (`PG_URL` constants are evaluated at import). Without xdist nothing changes.

It runs when this file is imported, not in `pytest_configure`: the conftests of the directories
named on the command line are imported before any `pytest_configure`, and they read the variable.

Every test also leaves structlog's configuration as it found it, its processors list the same
instance (`kept_structlog_configuration`).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager
from pathlib import Path

import pytest

#: The advisory lock that serialises `CREATE DATABASE`: copying `template1` fails when another
#: session creates a database from it at the same moment.
_CREATE_DATABASE_LOCK = 4_204_201


async def _ensure_database(url: str, name: str) -> None:
    import asyncpg

    connection = await asyncpg.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        await connection.execute(f"SELECT pg_advisory_lock({_CREATE_DATABASE_LOCK})")
        if not await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name):
            await connection.execute(f'CREATE DATABASE "{name}"')
        await connection.execute(f"SELECT pg_advisory_unlock({_CREATE_DATABASE_LOCK})")
    finally:
        await connection.close()


def _own_database_per_worker() -> None:
    worker = os.environ.get("PYTEST_XDIST_WORKER")
    url = os.environ.get("TRANIBOR_TEST_DATABASE_URL", "")
    # Imported twice (a second import path), it must not suffix the name twice.
    if (
        not worker
        or not url.startswith("postgresql")
        or os.environ.get("TRANIBOR_TEST_DATABASE_WORKER") == worker
    ):
        return
    from sqlalchemy.engine import make_url

    own = make_url(url).set(database=f"{make_url(url).database}_{worker}")
    asyncio.run(_ensure_database(url, str(own.database)))
    os.environ["TRANIBOR_TEST_DATABASE_URL"] = own.render_as_string(hide_password=False)
    os.environ["TRANIBOR_TEST_DATABASE_WORKER"] = worker


_own_database_per_worker()


@contextlib.contextmanager
def kept_structlog_configuration() -> Iterator[None]:
    """structlog's configuration as it was before, whatever ran inside: the same processors list,
    its contents put back, and the same factory, wrapper, context class and caching.

    A logger cached at first use keeps the processors list it was created with, and
    `structlog.testing.capture_logs` empties and fills the *current* list in place: it captures a
    cached logger only while the two are one list. A test that left another list behind
    (`structlog.reset_defaults()`, a `configure` with a new list), or a writer pointing at its own
    buffer, used to empty the captures of the tests run after it in the same worker (#1717).
    """
    import structlog

    config = structlog.get_config()
    processors = config["processors"]
    contents = list(processors)
    try:
        yield
    finally:
        processors[:] = contents
        structlog.configure(
            processors=processors,
            wrapper_class=config["wrapper_class"],
            context_class=config["context_class"],
            logger_factory=config["logger_factory"],
            cache_logger_on_first_use=config["cache_logger_on_first_use"],
        )


@pytest.fixture(autouse=True)
def _structlog_configuration_put_back() -> Iterator[None]:
    with kept_structlog_configuration():
        yield


@pytest.fixture
def structlog_configuration_kept() -> Callable[[], AbstractContextManager[None]]:
    """The context manager every test runs in, for a test that proves it."""
    return kept_structlog_configuration


@pytest.fixture(autouse=True)
def _runner_git_dirs(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The runner keeps its git directory outside the tree (S65-04c, ADR 0146 section 5), by default under
    the fixed `/var/tmp/choregos-git`: owned by whichever account ran it first, so a test that reaches the
    runner (the runner's own, the orchestrator's `test_remote_agent_runner_seam`) failed with `Permission
    denied` on a shared machine (#2011). Every test points the root into its own temporary directory instead;
    the production default is untouched. `tests/test_runner_git_dir_in_tests.py` guards it."""
    root = tmp_path_factory.mktemp("git-dirs")
    monkeypatch.setenv("TRANIBOR_RUNNER_GIT_DIR_ROOT", str(root))
    return root


@pytest.fixture(autouse=True)
def _git_independent_of_the_developers_config(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every `git` a test runs ignores the machine's git configuration (#1897): a global
    `commit.gpgsign=true` with no usable key failed every `git commit` in a temporary repository
    (exit 128) on a developer machine while CI was green. The global file is replaced by a neutral one
    (an identity, no signing), the system file is skipped; a test that sets its own identity or
    `-c` options still wins."""
    config = tmp_path_factory.mktemp("git-config") / "gitconfig"
    config.write_text(
        "[user]\n\tname = test\n\temail = test@example.org\n[commit]\n\tgpgsign = false\n[tag]\n\tgpgsign = false\n"
        "[init]\n\tdefaultBranch = main\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
