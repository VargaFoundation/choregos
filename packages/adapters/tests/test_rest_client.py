"""Le client REST partagé par Jira et GitLab : backoff borné, erreurs nommées, pagination."""

from __future__ import annotations

import httpx
import pytest
from choregos_adapters.errors import UpstreamError
from choregos_adapters.http import rest
from choregos_adapters.http.rest import RestClient

from ._transport import Fil, sans_attente


async def test_un_429_est_rejoue_en_respectant_retry_after(monkeypatch: pytest.MonkeyPatch) -> None:
    attentes = sans_attente(monkeypatch, rest)
    fil = Fil(
        {
            ("GET", "/x"): [
                httpx.Response(429, headers={"Retry-After": "7"}, text="slow down"),
                (200, {"ok": True}),
            ]
        }
    )
    client = RestClient("https://api.test", client=fil.client(), service="jira")
    assert await client.request("GET", "/x") == {"ok": True}
    assert attentes == [7.0], "l'attente est celle que le serveur demande, pas un exponentiel"
    assert len(fil.requetes) == 2


async def test_le_backoff_est_borne_et_finit_par_nommer_le_service(monkeypatch: pytest.MonkeyPatch) -> None:
    attentes = sans_attente(monkeypatch, rest)
    fil = Fil({("GET", "/x"): (503, "maintenance")})
    client = RestClient("https://api.test", client=fil.client(), service="gitlab")
    with pytest.raises(UpstreamError) as exc:
        await client.request("GET", "/x")
    assert exc.value.service == "gitlab" and exc.value.status_code == 503
    assert "503 sur GET /x" in str(exc.value)
    assert len(attentes) == rest.MAX_RETRIES and all(a <= 30.0 for a in attentes)


async def test_une_erreur_du_client_n_est_pas_rejouee() -> None:
    fil = Fil({("POST", "/x"): (400, {"errors": ["summary manquant"]})})
    client = RestClient("https://api.test", client=fil.client())
    with pytest.raises(UpstreamError) as exc:
        await client.request("POST", "/x", json={})
    assert len(fil.requetes) == 1, "un 400 ne changera pas en réessayant"
    assert "summary manquant" in str(exc.value)


async def test_un_204_rend_un_dictionnaire_vide_et_les_en_tetes_se_cumulent() -> None:
    fil = Fil({("DELETE", "/x"): (204, None)})
    client = RestClient("https://api.test", headers={"X-Atlassian-Token": "no-check"}, client=fil.client())
    assert await client.request("DELETE", "/x", headers={"X-Extra": "1"}) == {}
    envoyes = fil.requetes[0].headers
    assert envoyes["X-Atlassian-Token"] == "no-check" and envoyes["X-Extra"] == "1"
    assert envoyes["Accept"] == "application/json"


async def test_la_pagination_s_arrete_a_la_page_incomplete_et_au_plafond() -> None:
    pleine = [{"id": i} for i in range(100)]
    fil = Fil({("GET", "/items"): [(200, pleine), (200, pleine[:3])]})
    client = RestClient("https://api.test", client=fil.client())
    tout = await client.paginate("/items", params={"state": "opened"})
    assert len(tout) == 103
    pages = [r.url.params["page"] for r in fil.requetes]
    assert pages == ["1", "2"] and fil.requetes[0].url.params["state"] == "opened"
    fil2 = Fil({("GET", "/items"): (200, pleine)})
    assert (
        len(await RestClient("https://api.test", client=fil2.client()).paginate("/items", limit=150)) == 150
    )
