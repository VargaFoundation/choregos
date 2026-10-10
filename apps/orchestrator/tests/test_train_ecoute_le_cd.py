"""Le train écoute Argo CD pendant le départ (#280, S22-18).

Le webhook `/webhooks/argocd` signale `deploy_event` au train de l'environnement. Jusqu'ici le train
rangeait l'événement et ne le lisait jamais : une application annoncée dégradée pendant un soak de
20 minutes attendait la prochaine scrutation de l'étape. Ici, l'étape est tenue ouverte (son
observation ne rend pas la main) : seul l'événement peut faire revenir le départ en arrière.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
from collections.abc import Iterator
from typing import Any

import pytest

from .conftest import Fixture

pytestmark = pytest.mark.asyncio

HISTORIQUES = pathlib.Path(__file__).resolve().parents[3] / "tests" / "replay" / "histories"


class EtapesTenues:
    """L'observation des étapes nommées ne rend la main que sur `liberer()` : en simulé elle est
    instantanée, et la scrutation conclurait avant que le test ait pu envoyer quoi que ce soit."""

    def __init__(self, *etapes: str) -> None:
        self.etapes = set(etapes)
        self.en_cours: set[str] = set()
        self._libre = asyncio.Event()

    async def observer(self, seconds: float) -> None:
        from temporalio import activity

        etape = activity.info().activity_type
        if etape not in self.etapes:
            return
        self.en_cours.add(etape)
        try:
            await self._libre.wait()
        finally:
            self.en_cours.discard(etape)

    def liberer(self) -> None:
        self._libre.set()


@pytest.fixture
def tenues(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from choregos_orchestrator.activities import train as train_activities

    crees: list[EtapesTenues] = []

    def tenir(*etapes: str) -> EtapesTenues:
        tenue = EtapesTenues(*etapes)
        crees.append(tenue)
        monkeypatch.setattr(train_activities, "_observe", tenue.observer)
        return tenue

    yield tenir
    for tenue in crees:
        tenue.liberer()


def _argocd(type_: str, *, env: str = "prod", app: str = "billing-api", n: int = 1) -> dict[str, Any]:
    """Ce que `/webhooks/argocd` signale au train (un `InboundEvent` sérialisé)."""
    return {
        "type": type_,
        "source": "argocd",
        "delivery_id": f"argo-{n}",
        "project_slug": "billing-api",
        "payload": {"app": app, "revision": "abc123", "env": env},
    }


async def _train(env: Any, setup: Fixture) -> Any:
    return await env.client.start_workflow(
        "ReleaseTrain",
        {"project_slug": setup.project_slug, "env": "prod"},
        id=f"train-{setup.project_slug}-prod",
        task_queue="test",
    )


async def _jusqu_a(predicat: Any, timeout: float = 30.0) -> None:
    for _ in range(int(timeout * 10)):
        if await predicat():
            return
        await asyncio.sleep(0.1)
    raise AssertionError("condition jamais atteinte")


async def _etat(handle: Any) -> dict[str, Any]:
    return dict(await handle.query("status_query"))


async def _releases() -> list[Any]:
    from choregos_api.db.models import Release
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        return list((await session.execute(select(Release))).scalars())


async def test_une_degradation_annoncee_pendant_le_soak_fait_revenir_en_arriere_sans_attendre(
    setup: Fixture, temporal_env: Any, worker_factory: Any, tenues: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    soak = tenues("soak")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#7", "sha": "f1"})
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(lambda: _vrai("soak" in soak.en_cours))
            assert (await _etat(train))["status"] == "staging"

            await train.signal("deploy_event", _argocd("cd.app.degraded"))
            # Le soak est toujours tenu : seul l'événement peut avoir décidé du rollback.
            await _jusqu_a(lambda: _etat_gele(train), timeout=20)
            assert "soak" in soak.en_cours, "le soak n'a pas conclu : c'est l'événement qui a décidé"
            etat = await _etat(train)
            assert etat["freeze_reason"] == "Argo CD reported billing-api degraded"

            await train.signal("abort", {"by": "test"})
            soak.liberer()
            await train.result()
            historique = await train.fetch_history()

    (release,) = await _releases()
    assert release.status == "rolled_back"
    assert release.verdict == {"go": False, "reason": "Argo CD reported billing-api degraded"}
    assert "billing-api" in setup.adapters.cd.aborted, "le rollback a annulé le rollout"
    titres = [message.title for _, message in setup.adapters.notify.sent]
    assert not any(t.startswith("Approval requested") for t in titres), "le départ s'est arrêté au soak"

    # L'historique rejoue contre le code courant — et s'archive pour les suivants.
    from choregos_orchestrator.workflows import ALL_WORKFLOWS
    from temporalio.worker import Replayer

    await Replayer(workflows=ALL_WORKFLOWS).replay_workflow(historique)
    if os.environ.get("CHOREGOS_ARCHIVER_HISTORIQUE"):
        cible = HISTORIQUES / "train-ecoute-le-cd-S22-18.json"
        cible.write_text(json.dumps(json.loads(historique.to_json()), indent=1) + "\n", encoding="utf-8")


async def test_un_rollout_abandonne_pendant_le_canary_fait_revenir_en_arriere(
    setup: Fixture, temporal_env: Any, worker_factory: Any, tenues: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    canary = tenues("promote_canary_step")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#8", "sha": "g1"})
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(lambda: _statut(train, "awaiting_approval"))
            await train.signal("approve", {"by": "marie"})
            await _jusqu_a(lambda: _vrai("promote_canary_step" in canary.en_cours))

            await train.signal("deploy_event", _argocd("cd.rollout.aborted"))
            await _jusqu_a(lambda: _etat_gele(train), timeout=20)
            etat = await _etat(train)
            assert etat["freeze_reason"] == "Argo CD reported the billing-api rollout aborted"

            await train.signal("abort", {"by": "test"})
            canary.liberer()
            await train.result()

    (release,) = await _releases()
    assert release.status == "rolled_back"


async def test_le_train_n_ecoute_que_son_environnement_et_ses_applications(
    setup: Fixture, temporal_env: Any, worker_factory: Any, tenues: Any
) -> None:
    setup.adapters.cd.set_health("billing-api", "Healthy")
    soak = tenues("soak")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await train.signal("merged", {"work_item_key": "varga/billing-api#9", "sha": "h1"})
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(lambda: _vrai("soak" in soak.en_cours))

            for n, evenement in enumerate(
                (
                    _argocd("cd.app.degraded", env="staging"),
                    _argocd("cd.app.degraded", app="autre-app"),
                    _argocd("cd.app.synced"),
                    _argocd("cd.rollout.completed"),
                ),
                start=1,
            ):
                await train.signal("deploy_event", {**evenement, "delivery_id": f"argo-{n}"})
            await asyncio.sleep(1.5)
            etat = await _etat(train)
            assert etat["status"] == "staging" and etat["frozen"] is False, etat

            # La scrutation, elle, conclut normalement : le départ va jusqu'à l'approbation.
            soak.liberer()
            await _jusqu_a(lambda: _statut(train, "awaiting_approval"))
            assert (await _etat(train))["frozen"] is False
            await train.signal("abort", {"by": "test"})
            await train.result()

    (release,) = await _releases()
    assert release.status != "rolled_back"
    assert not setup.adapters.cd.aborted


async def test_une_alerte_recue_avant_le_depart_ne_le_fait_pas_revenir_en_arriere(
    setup: Fixture, temporal_env: Any, worker_factory: Any
) -> None:
    """Ce qu'Argo CD annonce pendant la collecte parle de la release d'avant : le départ suivant ne
    la relit pas (le smoke test regarde l'état réel juste après la promotion)."""
    setup.adapters.cd.set_health("billing-api", "Healthy")
    with temporal_env.auto_time_skipping_disabled():
        async with worker_factory():
            train = await _train(temporal_env, setup)
            await _jusqu_a(lambda: _statut(train, "collecting"))
            await train.signal("deploy_event", _argocd("cd.app.degraded"))
            await train.signal("merged", {"work_item_key": "varga/billing-api#10", "sha": "i1"})
            await train.signal("depart_now", {"by": "augustin"})
            await _jusqu_a(lambda: _statut(train, "awaiting_approval"))
            await train.signal("approve", {"by": "marie"})
            await _jusqu_a(lambda: _statut(train, "collecting"), timeout=60)
            assert (await _etat(train))["frozen"] is False
            await train.signal("abort", {"by": "test"})
            await train.result()

    (release,) = await _releases()
    assert release.status == "done"


async def _vrai(valeur: bool) -> bool:
    return valeur


async def _statut(handle: Any, statut: str) -> bool:
    return (await _etat(handle))["status"] == statut


async def _etat_gele(handle: Any) -> bool:
    return bool((await _etat(handle))["frozen"])
