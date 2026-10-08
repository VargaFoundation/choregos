"""Le `cd` de démonstration (S21-24) : ce que promeut le train d'un projet de démonstration, servi
par le processus des faux pour que l'orchestrateur et l'API voient le même environnement."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.fakes.cd_de_demo import CdDeDemo, FakeCdDeDemo
from choregos_adapters.fakes.rh import Guichet, GuichetDistant
from choregos_adapters.fakes.serveur import ApplicationDeDemo
from choregos_adapters.mcp import ClientMcp
from choregos_core.domain import Change

JETON = "jeton-des-faux"


def _servi(app: ApplicationDeDemo, jeton: str = JETON) -> CdDeDemo:
    client = ClientMcp("http://demo/cd/mcp", token=jeton, transport=httpx.ASGITransport(app=app))
    return CdDeDemo(GuichetDistant(client))


async def test_le_train_promeut_et_l_api_relit_le_meme_environnement() -> None:
    app = ApplicationDeDemo(jeton=JETON)
    orchestrateur, api = _servi(app), _servi(app)
    assert (await api.health("dev")).status == "Missing", "jamais promue : pas saine"

    ref = await orchestrateur.promote("prod", [Change(app="dev", tag="R-2026.10.07-1")], "R-2026.10.07-1")
    assert ref.merged and ref.url == "demo://prod/1"
    sante = await api.health("dev")
    assert (sante.status, sante.revision) == ("Healthy", "R-2026.10.07-1")
    assert (await api.rollout_status("dev")).phase == "Healthy"
    assert await api.current_revision("dev") == "R-2026.10.07-1"

    # Une activité rejouée ne promeut pas deux fois.
    await orchestrateur.promote("prod", [Change(app="dev", tag="R-2026.10.07-1")], "R-2026.10.07-1")
    assert len(app.faux["cd"].promotions) == 1


async def test_une_promotion_cassee_expres_se_voit_et_se_defait() -> None:
    """Pour montrer un rollback : la promotion suivante arrive dégradée ; le train l'annule."""
    faux = FakeCdDeDemo()
    cd = CdDeDemo(Guichet(faux))
    await cd.guichet.executer("break_next_rollout", {})
    await cd.promote("prod", [Change(app="dev", tag="R-1")], "R-1")
    assert (await cd.health("dev")).status == "Degraded"
    assert (await cd.rollout_status("dev")).phase == "Degraded"
    await cd.abort_rollout("dev")
    assert (await cd.rollout_status("dev")).phase == "Aborted"

    await cd.promote("prod", [Change(app="dev", tag="R-2")], "R-2")
    assert (await cd.health("dev")).status == "Healthy", "seule la promotion suivante était cassée"


async def test_une_lecture_n_ecrit_rien() -> None:
    faux = FakeCdDeDemo()
    cd = CdDeDemo(Guichet(faux))
    avant = faux.etat()
    await cd.health("dev")
    await cd.rollout_status("dev")
    await cd.current_revision("dev")
    assert faux.etat() == avant


def test_le_type_demo_est_refuse_en_staging_et_en_prod_et_joint_le_processus_des_faux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_adapters import available, build

    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    assert "demo" in available("cd")
    for environnement in ("staging", "prod"):
        monkeypatch.setenv("CHOREGOS_ENV", environnement)
        with pytest.raises(ConfigurationError, match="refusé"):
            build("cd", "demo", {})
    monkeypatch.setenv("CHOREGOS_ENV", "dev")
    distant: Any = build("cd", "demo", {"url": "http://choregos-demo-fakes:8090/cd/mcp", "api_key": JETON})
    assert isinstance(distant, CdDeDemo) and isinstance(distant.guichet, GuichetDistant)
    assert distant.guichet.client.url == "http://choregos-demo-fakes:8090/cd/mcp"
    local: Any = build("cd", "demo", {})
    assert isinstance(local.guichet, Guichet), "sans url : en mémoire"
