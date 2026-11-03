"""The project's allowed agent backends are applied at stage start (S47-20).

`agent.allowed_backends` was read only to pick a cross-backend reviewer. `prepare_stage` now
refuses a stage whose backend the project does not list, before any budget, key or pod, with the
same typed `BackendDisabled` error as a backend the platform disabled.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.exceptions import ApplicationError

from .conftest import Fixture
from .test_backend_toggle_enforced import _switch
from .test_budget_reservation import _plan
from .test_interpreter import _runs

pytestmark = pytest.mark.asyncio


async def _allow(setup: Fixture, backends: list[str]) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        config = dict(project.config)
        config["agent"] = {**dict(config.get("agent") or {}), "allowed_backends": backends}
        project.config = config


def _bundle(allowed: list[str]) -> Any:
    return SimpleNamespace(config=SimpleNamespace(agent=SimpleNamespace(allowed_backends=allowed)))


async def test_a_backend_outside_the_project_list_is_refused_before_any_key(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _switch("codex", enabled=True)
    await _allow(setup, ["claude-code"])
    with pytest.raises(ApplicationError) as refused:
        await prepare_stage({**_plan(setup), "backend": "codex"})
    assert refused.value.type == "BackendDisabled"
    assert refused.value.non_retryable
    assert "is not one this project allows" in str(refused.value)
    assert "codex" in str(refused.value)
    assert not setup.adapters.gateway.keys, "no key is minted"
    assert await _runs(setup) == 0
    assert setup.adapters.executor.started == [], "no runner pod"


async def test_the_allowlist_is_checked_before_the_platform_switch(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _switch("codex", enabled=False, reason="under security review")
    await _allow(setup, ["claude-code"])
    with pytest.raises(ApplicationError) as refused:
        await prepare_stage({**_plan(setup), "backend": "codex"})
    assert "is not one this project allows" in str(refused.value)
    assert "disabled by the platform administrator" not in str(refused.value)


async def test_an_empty_list_allows_every_enabled_backend(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import prepare_stage

    await _switch("codex", enabled=True)
    await _allow(setup, [])
    assert "refused" not in await prepare_stage({**_plan(setup), "backend": "codex"})


async def test_a_runtime_that_fixes_its_backend_is_not_concerned(setup: Fixture) -> None:
    from choregos_orchestrator.activities.stage import _require_backend_allowed

    code_agent = SimpleNamespace(spec=SimpleNamespace(runtime=SimpleNamespace(kind="code")))
    _require_backend_allowed(code_agent, _bundle(["claude-code"]), "codex")
    with pytest.raises(ApplicationError):
        _require_backend_allowed(None, _bundle(["claude-code"]), "codex")
