"""Le worker charge les greffons (S20-08) : leurs EFFETS d'actions gouvernées s'exécutent chez lui.

Il ne les chargeait pas. L'API connaissait l'effet d'un greffon pour le proposer ; le worker, qui
devait le jouer, le disait « inconnu » — l'action échouait au moment même de servir.
"""

from __future__ import annotations

import pytest


class _Arret(Exception):  # noqa: N818 - le signal d'arrêt du test
    pass


async def test_le_worker_charge_les_greffons_avant_de_joindre_temporal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import choregos_adapters
    from choregos_orchestrator import worker
    from temporalio.client import Client

    ordre: list[str] = []
    monkeypatch.setattr(choregos_adapters, "charger_les_greffons", lambda: ordre.append("greffons") or [])

    async def joindre(*_: object, **__: object) -> None:
        ordre.append("temporal")
        raise _Arret

    monkeypatch.setattr(Client, "connect", joindre)
    with pytest.raises(_Arret):
        await worker.run_worker(["orchestrator"])
    assert ordre == ["greffons", "temporal"]
