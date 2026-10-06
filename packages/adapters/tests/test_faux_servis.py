"""Les faux du scénario RH servis par un seul processus (S20-07) : ce qu'un déploiement à plusieurs
processus — l'API et l'orchestrateur du dev — doit voir pareil.

Un faux en mémoire tient son état dans son processus : l'orchestrateur inscrivait un poste que
l'API, relisant le parc, ne voyait pas. Servis par `ApplicationDeDemo`, deux clients — deux
processus — voient le même parc, le même annuaire.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes.rh import GuichetDistant
from choregos_adapters.fakes.serveur import ApplicationDeDemo
from choregos_adapters.mcp import ClientMcp, ErreurMcp

JETON = "jeton-des-faux"


def _guichet(app: ApplicationDeDemo, famille: str, jeton: str = JETON) -> GuichetDistant:
    return GuichetDistant(
        ClientMcp(f"http://demo/{famille}/mcp", token=jeton, transport=httpx.ASGITransport(app=app))
    )


async def test_deux_processus_voient_le_meme_parc() -> None:
    app = ApplicationDeDemo(jeton=JETON)
    orchestrateur, api = _guichet(app, "mdm"), _guichet(app, "mdm")
    inscrit = await orchestrateur.executer("enroll_device", {"serial": "PC-1", "upn": "lea@acme.test"})
    assert inscrit["enrolled"] is True
    assert (await api.executer("device_status", {"serial": "PC-1"}))["state"] == "enrolled"
    with pytest.raises(AdapterError, match="inscrit pour un autre utilisateur"):
        await api.executer("enroll_device", {"serial": "PC-1", "upn": "paul@acme.test"})


async def test_le_faux_graph_se_joint_par_son_adresse_et_son_secret() -> None:
    from choregos_adapters.identity import EntraIdentity

    app = ApplicationDeDemo(jeton=JETON)

    def annuaire(secret: str) -> EntraIdentity:
        return EntraIdentity(
            tenant_id="acme",
            client_id="choregos",
            client_secret=secret,
            administrative_unit_id=app.annuaire.unite,
            graph_url="http://demo/graph/v1.0",
            login_url="http://demo/login",
            transport=httpx.ASGITransport(app=app),
        )

    cree = await annuaire(JETON).create_user("lea@acme.test", "Léa Martin")
    relu = await annuaire(JETON).get_user("lea@acme.test")
    assert relu is not None and relu["id"] == cree["id"], "un autre client relit le compte créé"
    with pytest.raises(AdapterError, match="jeton refusé"):
        await annuaire("un-autre-secret").get_user("lea@acme.test")


async def test_un_jeton_faux_est_refuse_et_l_application_se_tient() -> None:
    app = ApplicationDeDemo(jeton=JETON)
    with pytest.raises(ErreurMcp) as refus:
        await _guichet(app, "access_control", jeton="faux").executer("badge_status", {"uid": "04A1"})
    assert refus.value.code == 401
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://demo") as http:
        assert (await http.get("/healthz")).json() == {"ok": True}
        assert (await http.post("/ailleurs/mcp", json={})).status_code == 404


def test_un_connecteur_demo_qui_porte_une_url_joint_le_processus_des_faux(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_adapters import build
    from choregos_adapters.fakes.rh import Guichet

    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    monkeypatch.setenv("CHOREGOS_ENV", "dev")
    distant: Any = build("mdm", "demo", {"url": "http://choregos-demo-fakes:8090/mdm/mcp", "api_key": JETON})
    assert isinstance(distant, GuichetDistant)
    assert distant.client.url == "http://choregos-demo-fakes:8090/mdm/mcp"
    assert isinstance(build("mdm", "demo", {}), Guichet), "sans url : en mémoire"
    monkeypatch.setenv("CHOREGOS_FAKES", "1")
    assert isinstance(build("mdm", "demo", {"url": "http://ailleurs/mcp"}), Guichet), (
        "sous CHOREGOS_FAKES, rien ne sort du processus"
    )
