"""L'`ActionWorkflow` (ADR 0035, S20-01) : une action approuvée s'exécute effet par effet, chacun
consigné sous sa clé ; une panne passagère est retentée sans refaire ce qui est fait ; un refus
arrête la suite et ce qui était fait est compensé, à rebours.
"""

from __future__ import annotations

import json
import os
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

from .conftest import Fixture

HISTORIQUES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


class Journal:
    """Ce que les faux effets ont vraiment fait, dans l'ordre — la vérité du monde extérieur."""

    def __init__(self) -> None:
        self.faits: list[tuple[str, dict[str, Any]]] = []
        self.pannes: dict[str, int] = {}


@pytest.fixture
def journal() -> Iterator[Journal]:
    from choregos_api import effets
    from choregos_api.effets import EffetRefuse

    vu = Journal()

    async def creer(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        vu.faits.append(("creer", params))
        return {"id": f"compte-{params['upn']}"}

    async def ajouter(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        reste = vu.pannes.get("ajouter", 0)
        if reste:
            vu.pannes["ajouter"] = reste - 1
            raise RuntimeError("503 du fournisseur, passager")
        vu.faits.append(("ajouter", params))
        return {"ajoute": params["groupe"]}

    async def refuser(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        vu.faits.append(("refuser", params))
        raise EffetRefuse("le compte est hors de l'unité administrative")

    async def defaire(_ctx: Any, params: dict[str, Any]) -> dict[str, Any]:
        vu.faits.append(("defaire", params))
        return {}

    for nom, fonction in (
        ("test.creer", creer),
        ("test.ajouter", ajouter),
        ("test.refuser", refuser),
        ("test.defaire", defaire),
    ):
        effets.declarer_un_effet(nom, fonction)
    yield vu
    effets.reinitialiser()


CREER = {
    "effect": "test.creer",
    "with": {"upn": "{{ params.upn }}"},
    "compensate": {"effect": "test.defaire", "with": {"desactiver": "{{ result.id }}"}},
}
AJOUTER = {
    "effect": "test.ajouter",
    "with": {"compte": "{{ effects[0].id }}", "groupe": "devs"},
    "compensate": {"effect": "test.defaire", "with": {"retirer": "devs"}},
}
SANS_COMPENSATION = {"effect": "test.ajouter", "with": {"compte": "{{ effects[0].id }}", "groupe": "vpn"}}
REFUSER = {"effect": "test.refuser", "with": {}}  # fmt: skip


async def _action(setup: Fixture, effets: list[dict[str, Any]]) -> str:
    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        action = Action(
            org_id=projet.org_id,
            project_id=projet.id,
            origin="transition",
            kind="arrivee.comptes",
            title="Les comptes de Léa",
            params={"upn": "lea@acme.test"},
            effects=effets,
            proposed_by={"kind": "system", "id": "system"},
            approval={},
            decisions=[],
            status="approved",
        )
        session.add(action)
        await session.flush()
        return str(action.id)


async def _executer(temporal_env: Any, worker_factory: Any, action_id: str) -> tuple[dict[str, Any], Any]:
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "ActionWorkflow", {"action_id": action_id}, id=f"action-{action_id}", task_queue="test"
        )
        return await handle.result(), await handle.fetch_history()


async def _etat(action_id: str) -> tuple[str, list[tuple[str, str, int]]]:
    from choregos_api.db.models import Action, ActionEffect
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        action = await session.get(Action, action_id)
        assert action is not None
        lignes = (
            await session.execute(
                select(ActionEffect)
                .where(ActionEffect.action_id == action_id)
                .order_by(ActionEffect.position)
            )
        ).scalars()
        return action.status, [(e.key.split(":")[1], e.status, e.attempts) for e in lignes]


async def test_une_panne_passagere_ne_refait_pas_l_effet_deja_fait(
    setup: Fixture, temporal_env: Any, worker_factory: Any, journal: Journal
) -> None:
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    journal.pannes["ajouter"] = 1
    action_id = await _action(setup, [CREER, AJOUTER])
    resultat, historique = await _executer(temporal_env, worker_factory, action_id)
    assert resultat == {"status": "succeeded", "effects": [0, 1]}
    assert journal.faits == [
        ("creer", {"upn": "lea@acme.test"}),
        ("ajouter", {"compte": "compte-lea@acme.test", "groupe": "devs"}),
    ], "le compte n'a été créé qu'une fois ; le second effet cite le premier"
    statut, effets = await _etat(action_id)
    assert statut == "succeeded"
    assert effets == [("0", "done", 1), ("1", "done", 2)], (
        "deux tentatives pour le second, une pour le premier"
    )

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        cible = HISTORIQUES / "action-S20-01.json"
        cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_un_refus_compense_ce_qui_etait_fait_a_rebours(
    setup: Fixture, temporal_env: Any, worker_factory: Any, journal: Journal
) -> None:
    action_id = await _action(setup, [CREER, AJOUTER, SANS_COMPENSATION, REFUSER])
    resultat, _ = await _executer(temporal_env, worker_factory, action_id)
    assert resultat["status"] == "failed" and "hors de l'unité" in resultat["error"]
    assert [nom for nom, _ in journal.faits] == [
        "creer",
        "ajouter",
        "ajouter",
        "refuser",
        "defaire",
        "defaire",
    ]
    assert journal.faits[-2:] == [
        ("defaire", {"retirer": "devs"}),
        ("defaire", {"desactiver": "compte-lea@acme.test"}),
    ]
    assert [c["undone"] for c in resultat["compensations"]] == [False, True, True], (
        "à rebours ; ce qu'il n'a pu défaire, il le dit"
    )
    statut, effets = await _etat(action_id)
    assert statut == "failed"
    assert [e[1] for e in effets] == ["compensated", "compensated", "compensation_failed", "started"]


async def test_une_cle_faite_ne_se_refait_pas_une_cle_commencee_se_reprend(
    setup: Fixture, journal: Journal
) -> None:
    """Le worker tué : avant la confirmation, l'effet se reprend (il est idempotent) ; après, la
    réponse consignée revient sans que rien ne reparte vers le système tiers."""
    from choregos_api.db.models import ActionEffect
    from choregos_api.db.session import session_scope
    from choregos_orchestrator.activities.actions import executer_l_effet

    action_id = await _action(setup, [CREER])
    async with session_scope() as session:
        from choregos_api.db.models import Action

        action = await session.get(Action, action_id)
        assert action is not None
        session.add(
            ActionEffect(
                org_id=action.org_id,
                action_id=action_id,
                position=0,
                key=f"{action_id}:0",
                effect="test.creer",
                params={},
                status="started",
                attempts=1,
            )
        )
    premiere = await executer_l_effet({"action_id": action_id, "position": 0})
    seconde = await executer_l_effet({"action_id": action_id, "position": 0})
    assert premiere["replayed"] is False and seconde["replayed"] is True
    assert seconde["result"] == {"id": "compte-lea@acme.test"}
    assert journal.faits == [("creer", {"upn": "lea@acme.test"})], "une seule création chez le tiers"
    _, effets = await _etat(action_id)
    assert effets == [("0", "done", 2)]


async def test_une_action_approuvee_atteint_le_serveur_avec_la_cle_du_connecteur(
    setup: Fixture, temporal_env: Any, worker_factory: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """De bout en bout côté orchestrateur (S20-02) : l'outil sous validation est devenu une action,
    elle a été approuvée ; l'`ActionWorkflow` l'exécute, et c'est LUI qui joint le serveur — avec
    la clé du connecteur, résolue par la plateforme."""
    import choregos_adapters
    from choregos_adapters.fakes.mcp import FakeMcpServer
    from choregos_api.db.models import ConnectorOperation, OrgConnector, Project
    from choregos_api.db.session import session_scope

    serveur = FakeMcpServer(
        outils=[{"name": "commander_poste", "inputSchema": {"type": "object"}}], jeton="cle-fournisseur"
    )
    monkeypatch.setattr(choregos_adapters, "FAUX_MCP", serveur)
    monkeypatch.setenv("CHOREGOS_TEST_CLE_FOURNISSEUR", "cle-fournisseur")
    async with session_scope() as session:
        projet = await session.get(Project, setup.project_id)
        assert projet is not None
        instance = OrgConnector(
            org_id=projet.org_id,
            name="fournisseur",
            kind="mcp",
            type="mcp",
            config={"url": "https://fournisseur.test/mcp"},
            secret_refs={"token": "env:CHOREGOS_TEST_CLE_FOURNISSEUR"},
        )
        session.add(instance)
        await session.flush()
        session.add(
            ConnectorOperation(
                org_id=projet.org_id,
                connector_id=instance.id,
                name="commander_poste",
                access="write",
                policy="approval",
                groups=[],
            )
        )
    effet = {
        "effect": "connector.call",
        "with": {
            "connector": "fournisseur",
            "operation": "commander_poste",
            "arguments": {"modele": "portable-14"},
        },
    }
    action_id = await _action(setup, [effet])
    resultat, _ = await _executer(temporal_env, worker_factory, action_id)
    assert resultat["status"] == "succeeded", resultat
    assert serveur.appels == [("commander_poste", {"modele": "portable-14"})]
    assert {entetes["authorization"] for _, entetes, _ in serveur.recues} == {"Bearer cle-fournisseur"}


async def test_une_action_demarree_avant_que_sa_ligne_soit_visible_attend_qu_elle_le_soit(
    setup: Fixture, temporal_env: Any, worker_factory: Any, journal: Journal
) -> None:
    """La requête qui décide démarre `action-<id>` AVANT de valider sa transaction (S20-08) : la ligne
    peut ne pas être encore visible quand le workflow la lit. Il retente, au lieu de mourir."""
    import asyncio

    from choregos_api.db.models import Action, Project
    from choregos_api.db.session import session_scope

    identifiant = "00000000-0000-7000-8000-00000000a7a1"
    async with worker_factory():
        handle = await temporal_env.client.start_workflow(
            "ActionWorkflow", {"action_id": identifiant}, id=f"action-{identifiant}", task_queue="test"
        )
        await asyncio.sleep(0.3)
        async with session_scope() as session:
            projet = await session.get(Project, setup.project_id)
            assert projet is not None
            session.add(
                Action(
                    id=identifiant,
                    org_id=projet.org_id,
                    project_id=projet.id,
                    origin="transition",
                    kind="arrivee.comptes",
                    title="Les comptes de Léa",
                    params={"upn": "lea@acme.test"},
                    effects=[CREER],
                    proposed_by={"kind": "system", "id": "system"},
                    approval={},
                    decisions=[],
                    status="approved",
                )
            )
        resultat = await handle.result()
    assert resultat["status"] == "succeeded"
    assert journal.faits == [("creer", {"upn": "lea@acme.test"})]
