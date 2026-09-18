"""La revue croisée est un mécanisme, pas une intention : le backend change vraiment."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_orchestrator.activities import stage as stage_activities

from .conftest import Fixture

pytestmark = pytest.mark.asyncio


async def _set_cross_backend(project_id: str, value: bool) -> None:
    from choregos_api.db.session import session_scope
    from choregos_api.services import active_policy, policy_model

    async with session_scope() as session:
        row = await active_policy(session, project_id)
        assert row is not None
        model = policy_model(row)
        model.review.cross_backend = value
        row.json_doc = model.model_dump(mode="json", exclude_none=True)


async def _implemented_by(setup: Fixture, backend: str) -> None:
    from choregos_api.db.models import Run
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        session.add(
            Run(
                work_item_id=setup.work_item_id,
                project_id=setup.project_id,
                stage_role="implement",
                attempt=1,
                backend=backend,
                status="succeeded",
            )
        )


def _plan(setup: Fixture, role: str, backend: str | None = None) -> dict[str, Any]:
    return {
        "project_id": setup.project_id,
        "work_item_id": setup.work_item_id,
        "transition_id": f"t-{role}",
        "role": role,
        "from_state": "in_progress",
        "to_state": "verifying",
        "actor": "checker",
        "attempt": 1,
        "backend": backend,
        "model_request": "profile:by_size",
    }


async def test_le_relecteur_n_est_pas_l_implementeur(setup: Fixture) -> None:
    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "openhands")

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="openhands"))

    backend = prepared["stage_input"]["agent"]["backend"]
    assert backend != "openhands", "la politique exige un autre relecteur"
    assert backend in {"claude-code", "codex", "gemini-cli", "goose", "opencode", "copilot-cli"}


async def test_sans_la_politique_le_backend_demande_est_respecte(setup: Fixture) -> None:
    await _set_cross_backend(setup.project_id, False)
    await _implemented_by(setup, "openhands")

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="openhands"))

    assert prepared["stage_input"]["agent"]["backend"] == "openhands"


async def test_un_relecteur_deja_different_n_est_pas_change(setup: Fixture) -> None:
    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "openhands")

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="goose"))

    assert prepared["stage_input"]["agent"]["backend"] == "goose"


async def test_les_autres_roles_ne_sont_pas_touches(setup: Fixture) -> None:
    """Seule la revue est croisée : l'implémentation garde le backend du projet."""
    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "openhands")

    prepared = await stage_activities.prepare_stage(_plan(setup, "implement", backend="openhands"))

    assert prepared["stage_input"]["agent"]["backend"] == "openhands"


async def test_un_seul_backend_autorise_degrade_sans_bloquer(setup: Fixture) -> None:
    """Un projet qui n'autorise qu'un backend ne doit pas voir ses tickets se bloquer."""
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope

    await _set_cross_backend(setup.project_id, True)
    await _implemented_by(setup, "openhands")
    async with session_scope() as session:
        project = await session.get(Project, setup.project_id)
        assert project is not None
        config = dict(project.config)
        config["agent"] = {"default_backend": "openhands", "allowed_backends": ["openhands"]}
        project.config = config

    prepared = await stage_activities.prepare_stage(_plan(setup, "review", backend="openhands"))

    assert prepared["stage_input"]["agent"]["backend"] == "openhands"
    assert prepared["run_id"], "le ticket avance malgré la revue dégradée"
