"""Who spent it (S29-01): the ledger names the agent and the workflow of each line.

The writers fill the columns; the migration backfills the lines older than them from their run and
their work item.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
import sqlalchemy as sa
from alembic import command
from choregos_api.config import get_settings
from httpx import AsyncClient

from .test_migrations import _config


def _insert(connexion: Any, table: str, **values: Any) -> None:
    """Inserts a row, filling every other NOT NULL column with a dummy: the backfill reads only
    the columns it joins on."""
    colonnes = sa.inspect(connexion).get_columns(table)
    ligne = dict(values)
    for colonne in colonnes:
        nom = colonne["name"]
        if nom in ligne or colonne["nullable"] or colonne.get("default") is not None:
            continue
        genre = colonne["type"]
        if isinstance(genre, sa.Boolean):
            ligne[nom] = 0
        elif isinstance(genre, sa.Integer | sa.Float):
            ligne[nom] = 1
        elif isinstance(genre, sa.DateTime):
            ligne[nom] = "2026-10-01 00:00:00"
        elif isinstance(genre, sa.JSON):
            ligne[nom] = "{}"
        else:
            ligne[nom] = f"x-{table}-{nom}"[:30]
    cles = ", ".join(ligne)
    marques = ", ".join(f":{k}" for k in ligne)
    connexion.execute(sa.text(f"INSERT INTO {table} ({cles}) VALUES ({marques})"), ligne)


def test_the_migration_backfills_a_seeded_ledger(tmp_path: pathlib.Path) -> None:
    fichier = tmp_path / "backfill.db"
    url = f"sqlite:///{fichier}"
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{fichier}")
    get_settings.cache_clear()
    try:
        config = _config(url)
        command.upgrade(config, "a3c5e7f9b1d2")
        moteur = sa.create_engine(url)
        ts = "2026-10-01 00:00:00"
        with moteur.begin() as c:
            _insert(c, "organizations", id="org1", slug="o", name="O")
            _insert(c, "projects", id="p1", org_id="org1", slug="p", name="P")
            _insert(c, "workflow_defs", id="wd1", project_id="p1", name="delivery", version=3, yaml="y")
            _insert(c, "work_items", id="wi1", project_id="p1", workflow_def_id="wd1", tracker_key="K-1")
            _insert(c, "work_items", id="wi2", project_id="p1", tracker_key="K-2")
            _insert(
                c,
                "runs",
                id="r1",
                work_item_id="wi1",
                project_id="p1",
                agent_slug="reviewer",
                agent_version=2,
            )
            _insert(c, "cost_ledger", id="l1", project_id="p1", work_item_id="wi1", run_id="r1", ts=ts)
            _insert(c, "cost_ledger", id="l2", project_id="p1", work_item_id="wi2", ts=ts)
            _insert(c, "cost_ledger", id="l3", project_id="p1", ts=ts)
        command.upgrade(config, "head")
        with moteur.connect() as c:
            lignes = {
                r[0]: tuple(r[1:])
                for r in c.execute(
                    sa.text(
                        "SELECT id, agent_slug, agent_version, workflow_name, workflow_version "
                        "FROM cost_ledger ORDER BY id"
                    )
                )
            }
    finally:
        monkeypatch.undo()
        get_settings.cache_clear()
    assert lignes["l1"] == ("reviewer", 2, "delivery", 3), "agent from the run, workflow from the work item"
    assert lignes["l2"] == (None, None, None, None), "a work item pinned to no definition stays empty"
    assert lignes["l3"] == (None, None, None, None), "no run, no item: nothing to say"


async def test_a_run_s_ledger_lines_carry_its_agent_and_workflow(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    from choregos_api.db.models import CostLedger, Run, WorkflowDef, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.services.couts import record_cost
    from sqlalchemy import select

    async with session_scope() as session:
        definition = (await session.execute(select(WorkflowDef))).scalars().first()
        assert definition is not None
        item = WorkItem(
            project_id=project["id"],
            tracker_key="B-1",
            title="T",
            state="ready",
            workflow_def_id=definition.id,
        )
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="r-dim",
                work_item_id=item.id,
                project_id=project["id"],
                stage_role="implement",
                status="running",
                agent_slug="coordinator",
                agent_version=4,
            )
        )
        await session.flush()
        await record_cost(
            session,
            project_id=project["id"],
            work_item_id=item.id,
            run_id="r-dim",
            provider="p",
            model="m",
            backend=None,
            stage_role="implement",
            size=None,
            tokens_in=1,
            tokens_out=1,
            tokens_cached=0,
            cost_usd=0.5,
            fx_rate=0.9,
        )
        await session.flush()
        ligne = (await session.execute(select(CostLedger))).scalar_one()
        assert (ligne.agent_slug, ligne.agent_version) == ("coordinator", 4)
        assert (ligne.workflow_name, ligne.workflow_version) == (definition.name, definition.version)
