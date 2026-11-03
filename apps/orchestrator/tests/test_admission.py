# SPDX-License-Identifier: Apache-2.0
"""Can an INSTALLED extension refuse a run's start — before any key, and never afterwards?

The extension is a real module with its `.dist-info`: `load_extensions()` finds it and calls its
`plug_in()`, as at a worker's startup.
"""

from __future__ import annotations

import pathlib
import sys
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import func, select

from .conftest import Fixture

EXTENSION = """
from choregos_core.admission import AdmissionRefused, declare_admission

#: What the test sets: the "suspended" organisation, and what the check saw.
STATE = {"suspended": set(), "seen": [], "outage": False}


async def organisation_is_active(request):
    STATE["seen"].append(request)
    if STATE["outage"]:
        raise ConnectionError("organisations database unreachable")
    if request.org in STATE["suspended"]:
        raise AdmissionRefused(f"organisation {request.org} is suspended — reactivate it, then relaunch")


def plug_in():
    declare_admission("suspension", organisation_is_active)
"""


@pytest.fixture
def extension(tmp_path: pathlib.Path) -> Iterator[Any]:
    from choregos_adapters import load_extensions
    from choregos_core.admission import reset

    root = tmp_path / "site"
    root.mkdir()
    (root / "extension_admission.py").write_text(EXTENSION, encoding="utf-8")
    info = root / "extension_admission-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: extension_admission\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\nextension_admission = extension_admission:plug_in\n", encoding="utf-8"
    )
    sys.path.insert(0, str(root))
    try:
        assert "extension_admission" in load_extensions()
        yield sys.modules["extension_admission"].STATE
    finally:
        sys.path.remove(str(root))
        sys.modules.pop("extension_admission", None)
        reset()


async def _prepare(setup: Fixture, attempt: int = 1) -> dict[str, Any]:
    from choregos_orchestrator.activities import stage as activities

    return await activities.prepare_stage(
        {
            "project_id": setup.project_id,
            "work_item_id": setup.work_item_id,
            "transition_id": "t-implement",
            "role": "implement",
            "actor": "dev",
            "from_state": "ready",
            "to_state": "in_progress",
            "attempt": attempt,
        }
    )


async def _count(model: Any) -> int:
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


async def test_the_check_receives_what_it_needs_to_decide(setup: Fixture, extension: Any) -> None:
    prepared = await _prepare(setup)
    assert prepared["run_id"]
    (request,) = extension["seen"]
    assert (request.org, request.project, request.work_item) == (
        "varga",
        setup.project_slug,
        setup.tracker_key,
    )
    assert request.run_id == prepared["run_id"] and request.role == "implement"
    assert request.budget_usd > 0


async def test_a_refusal_stops_the_stage_without_retry_and_without_a_key(
    setup: Fixture, extension: Any
) -> None:
    from choregos_api.db.models import GatewayKeyRow, Run
    from temporalio.exceptions import ApplicationError

    extension["suspended"].add("varga")
    with pytest.raises(ApplicationError) as refusal:
        await _prepare(setup)
    assert refusal.value.type == "admission_refused" and refusal.value.non_retryable
    # The reason names the check AND says what to do: it is what the work item displays.
    assert "suspension : organisation varga is suspended — reactivate it" in str(refusal.value)
    assert await _count(GatewayKeyRow) == 0, "a key was issued for a refused run"
    assert await _count(Run) == 0, "a run was created although it was refused"


async def test_an_already_prepared_run_is_never_refused_afterwards(setup: Fixture, extension: Any) -> None:
    """Replaying `prepare_stage` (Temporal resume) after a suspension does not kill what is running."""
    first = await _prepare(setup)
    extension["suspended"].add("varga")
    replayed = await _prepare(setup)
    assert replayed["reused"] and replayed["run_id"] == first["run_id"]


async def test_a_check_outage_is_not_a_refusal(setup: Fixture, extension: Any) -> None:
    """It comes up as it is, and Temporal retries: an unreachable database decides nothing."""
    from temporalio.exceptions import ApplicationError

    extension["outage"] = True
    with pytest.raises(ConnectionError):
        await _prepare(setup)
    try:
        await _prepare(setup)
    except ApplicationError:  # pragma: no cover - this would be the default
        pytest.fail("a check outage was turned into a final refusal")
    except ConnectionError:
        pass


async def test_without_an_extension_every_run_is_admitted(setup: Fixture) -> None:
    from choregos_core.admission import declared_admissions

    assert declared_admissions() == ()
    assert (await _prepare(setup))["run_id"]
