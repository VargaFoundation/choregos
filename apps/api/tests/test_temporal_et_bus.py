"""Le client Temporal réel face à un client simulé, et le bus d'événements en mémoire.

`RealTemporal.describe()` est ce qui rend un ticket mort visible (P0-2) ; il n'était
exercé que par le faux client. Ici un client Temporal est simulé objet par objet — la
forme que `temporalio` rend — pour prouver la traduction d'état, la cause lisible d'un
échec, et le repli « inconnu » quand Temporal ne répond pas.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from choregos_api.events import EventBus, _charge_utile, emit
from choregos_api.temporal import RealTemporal, _cause_lisible
from choregos_contracts import EventType


class _Handle:
    def __init__(self, statut: str | None, erreur: BaseException | None = None) -> None:
        self.statut = statut
        self.erreur = erreur
        self.signaux: list[tuple[str, Any]] = []
        self.annule = False

    async def describe(self) -> Any:
        status = SimpleNamespace(name=self.statut) if self.statut else None
        return SimpleNamespace(status=status)

    async def result(self) -> Any:
        if self.erreur:
            raise self.erreur
        return {}

    async def signal(self, nom: str, charge: Any) -> None:
        self.signaux.append((nom, charge))

    async def query(self, nom: str) -> Any:
        return {"query": nom}

    async def cancel(self) -> None:
        self.annule = True


class _Client:
    def __init__(self, handle: _Handle) -> None:
        self.handle = handle

    def get_workflow_handle(self, workflow_id: str) -> _Handle:
        return self.handle


def _temporal(handle: _Handle) -> RealTemporal:
    temporal = RealTemporal("temporal:7233", "default", "orchestrator")
    temporal._client = _Client(handle)
    return temporal


async def test_un_workflow_en_cours_se_decrit_sans_cause() -> None:
    etat = await _temporal(_Handle("RUNNING")).describe("wi-1")
    assert etat is not None and etat.status == "RUNNING" and etat.failure is None
    sans_statut = await _temporal(_Handle(None)).describe("wi-1")
    assert sans_statut is not None and sans_statut.status == "RUNNING"


async def test_un_workflow_echoue_porte_la_cause_la_plus_profonde() -> None:
    """« le projet n'a pas de dépôt », pas « Activity task failed »."""
    profonde = ValueError("le projet n'a pas de dépôt")
    milieu = RuntimeError("Activity task failed")
    milieu.__cause__ = profonde
    haut = RuntimeError("Workflow execution failed")
    haut.__cause__ = milieu
    etat = await _temporal(_Handle("FAILED", haut)).describe("wi-1")
    assert etat is not None and etat.status == "FAILED" and etat.failure == "le projet n'a pas de dépôt"
    assert _cause_lisible(SimpleNamespace(message="m", __cause__=None)) == "m"  # type: ignore[arg-type]


async def test_temporal_absent_ou_lent_repond_inconnu_plutot_que_de_tomber() -> None:
    class _Lent(_Handle):
        async def describe(self) -> Any:
            await asyncio.sleep(10)

    temporal = _temporal(_Lent("RUNNING"))
    with pytest.MonkeyPatch.context() as mp:
        # deux secondes d'attente maximum : on ne les paie pas dans un test
        import choregos_api.temporal as module

        original = module.asyncio.wait_for

        async def presse(coro: Any, timeout: float) -> Any:
            return await original(coro, 0.01)

        mp.setattr(module.asyncio, "wait_for", presse)
        assert await temporal.describe("wi-1") is None

    class _Casse(_Client):
        def get_workflow_handle(self, workflow_id: str) -> _Handle:
            raise OSError("connexion refusée")

    temporal._client = _Casse(_Handle("RUNNING"))
    assert await temporal.describe("wi-1") is None


async def test_signal_requete_et_annulation_passent_par_le_handle() -> None:
    handle = _Handle("RUNNING")
    temporal = _temporal(handle)
    await temporal.signal("wi-1", "freeze", {"reason": "x"})
    assert handle.signaux == [("freeze", {"reason": "x"})]
    assert await temporal.query("wi-1", "status_query") == {"query": "status_query"}
    await temporal.cancel("wi-1")
    assert handle.annule


async def test_le_bus_route_par_projet_sujet_et_run_et_rejoue_apres_un_identifiant() -> None:
    bus = EventBus(buffer_size=3)
    premier = bus_event(bus, EventType.RUN_STARTED, subject="r-1", project_slug="billing", run_id="r-1")
    second = bus_event(bus, EventType.RUN_PROGRESS, subject="r-1", project_slug="billing", run_id="r-1")
    bus_event(bus, EventType.WORKITEM_CREATED, subject="B-1", project_slug="autre")
    assert [e.id for e in bus.replay("run:r-1")] == [premier.id, second.id]
    assert [e.id for e in bus.replay("project:billing", after_id=premier.id)] == [second.id]
    assert len(bus.replay("*")) == 3 and bus.replay("subject:B-1")[0].project_slug == "autre"
    assert bus.replay("run:r-1", after_id="inconnu") == bus.replay("run:r-1"), "identifiant inconnu : tout"
    for _ in range(5):
        bus_event(bus, EventType.RUN_PROGRESS, subject="r-1", project_slug="billing", run_id="r-1")
    assert len(bus.replay("run:r-1")) == 3, "le tampon est borné"


async def test_un_abonne_recoit_le_rejeu_puis_le_direct() -> None:
    bus = EventBus()
    avant = bus_event(bus, EventType.RUN_STARTED, subject="r-1", run_id="r-1")
    recu: list[str] = []

    async def ecouter() -> None:
        flux = bus.subscribe("run:r-1")
        async for event in flux:
            recu.append(str(event.type))
            if len(recu) == 2:
                break
        # Un générateur asynchrone abandonné n'exécute son `finally` qu'à sa fermeture :
        # c'est ce que fait le serveur SSE quand le client part.
        await flux.aclose()

    tache = asyncio.create_task(ecouter())
    await asyncio.sleep(0.01)
    assert bus.subscriber_count("run:r-1") == 1
    bus_event(bus, EventType.RUN_PROGRESS, subject="r-1", run_id="r-1")
    await asyncio.wait_for(tache, 2)
    assert recu == [str(avant.type), str(EventType.RUN_PROGRESS)]
    assert bus.subscriber_count("run:r-1") == 0, "désabonné à la sortie"


def test_une_charge_trop_lourde_est_allegee_pour_la_notification() -> None:
    from choregos_api import events as module

    event = emit(
        EventType.RUN_PROGRESS,
        subject="r-1",
        run_id="r-1",
        state="running",
        texte="x" * (module.TAILLE_MAX + 100),
    )
    allege = _charge_utile(event)
    assert len(allege.encode()) <= module.TAILLE_MAX
    assert '"truncated":true' in allege and '"run_id":"r-1"' in allege and '"state":"running"' in allege
    court = emit(EventType.RUN_PROGRESS, subject="r-1", run_id="r-1")
    assert '"truncated"' not in _charge_utile(court)


def bus_event(bus: EventBus, type_: EventType, **data: Any) -> Any:
    from choregos_api.events import ChoregosEvent

    subject = data.pop("subject", None)
    project_slug = data.pop("project_slug", None)
    event = ChoregosEvent.emit(type_, source="/test", subject=subject, project_slug=project_slug, **data)
    bus.publish(event)
    return event
