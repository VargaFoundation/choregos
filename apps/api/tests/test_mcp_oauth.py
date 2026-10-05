"""La porte MCP, serveur de ressources OAuth (RFC 9728) : l'IdP émet les jetons (ADR 0030).

Le faux IdP de ces tests publie une découverte OIDC et un JWKS (respx), et signe ses jetons avec une
paire RSA générée ici : la porte doit refuser tout ce qu'un IdP réel ne lui aurait pas donné pour
elle — audience étrangère, `alg: none`, HS256, jeton expiré, émetteur faux — suivre une rotation de
clés, et ne connaître que les humains qui se sont déjà connectés à la console.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient

EMETTEUR = "https://idp.test/realms/plateforme"
AUDIENCE = "choregos-mcp"


def _paire() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(cle_privee: Any, kid: str) -> dict[str, Any]:
    publique = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(cle_privee.public_key()))
    return {**publique, "kid": kid, "use": "sig", "alg": "RS256"}


def _jeton(cle_privee: Any, kid: str = "k1", **surcharges: Any) -> str:
    maintenant = int(time.time())
    revendications = {
        "iss": EMETTEUR,
        "aud": AUDIENCE,
        "sub": "sub-admin",
        "azp": "claude-ai",
        "scope": "openid mcp:read",
        "iat": maintenant,
        "exp": maintenant + 300,
        **surcharges,
    }
    return jwt.encode(revendications, cle_privee, algorithm="RS256", headers={"kid": kid})


@pytest.fixture
def idp(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Any]]:
    from choregos_api.config import reset_settings_cache
    from choregos_api.mcp.oauth import CLES

    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_ENABLED", "true")
    # La porte suit l'IdP de la console : `mcp_oauth_issuer` vide.
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", EMETTEUR)
    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_AUDIENCE", AUDIENCE)
    reset_settings_cache()
    CLES.vider()
    etat: dict[str, Any] = {"k1": _paire(), "k2": _paire(), "publiees": ["k1"]}
    with respx.mock(assert_all_called=False) as faux:
        faux.get(f"{EMETTEUR}/.well-known/openid-configuration").mock(
            return_value=httpx.Response(200, json={"issuer": EMETTEUR, "jwks_uri": f"{EMETTEUR}/jwks"})
        )
        faux.get(f"{EMETTEUR}/jwks").mock(
            side_effect=lambda _r: httpx.Response(
                200, json={"keys": [_jwk(etat[kid], kid) for kid in etat["publiees"]]}
            )
        )
        faux.route(host="test").pass_through()
        yield etat
    CLES.vider()
    reset_settings_cache()


async def _relier_admin(sub: str = "sub-admin") -> None:
    from choregos_api.db.models import User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        admin = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        admin.oidc_sub = sub


async def _outils(client: AsyncClient, jeton: str) -> Any:
    return await client.post(
        "/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {jeton}"},
    )


async def test_eteinte_la_porte_ne_publie_pas_de_metadonnees(client: AsyncClient) -> None:
    assert (await client.get("/.well-known/oauth-protected-resource/mcp")).status_code == 404


async def test_les_metadonnees_disent_ou_obtenir_un_jeton(idp: dict[str, Any], client: AsyncClient) -> None:
    corps = (await client.get("/.well-known/oauth-protected-resource/mcp")).json()
    assert corps["resource"].endswith("/mcp") and corps["authorization_servers"] == [EMETTEUR]
    sans = await client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert sans.status_code == 401
    assert 'resource_metadata="' in sans.headers["www-authenticate"]


async def test_un_jeton_de_l_idp_ouvre_la_porte_en_lecture(
    idp: dict[str, Any], client: AsyncClient, project: dict[str, Any]
) -> None:
    await _relier_admin()
    client.cookies.clear()
    reponse = await _outils(client, _jeton(idp["k1"]))
    assert reponse.status_code == 200, reponse.text
    noms = {o["name"] for o in reponse.json()["result"]["tools"]}
    assert "list_projects" in noms and "create_work_item" not in noms, "scope mcp:read : pas d'écriture"


@pytest.mark.parametrize(
    "fabrique",
    [
        pytest.param(lambda k: _jeton(_paire()), id="signature-forgee"),
        pytest.param(lambda k: _jeton(k, aud="une-autre-application"), id="audience-etrangere"),
        pytest.param(lambda k: _jeton(k, iss="https://ailleurs.test"), id="emetteur-faux"),
        pytest.param(lambda k: _jeton(k, exp=int(time.time()) - 60), id="expire"),
        pytest.param(
            lambda k: jwt.encode({"sub": "sub-admin", "aud": AUDIENCE}, "secret", algorithm="HS256"),
            id="hs256",
        ),
        pytest.param(
            lambda k: jwt.encode({"sub": "sub-admin", "aud": AUDIENCE}, None, algorithm="none"), id="alg-none"
        ),
    ],
)
async def test_ce_que_l_idp_n_a_pas_donne_pour_la_porte_est_refuse(
    idp: dict[str, Any], client: AsyncClient, admin: str, fabrique: Any
) -> None:
    await _relier_admin()
    client.cookies.clear()
    reponse = await _outils(client, fabrique(idp["k1"]))
    assert reponse.status_code == 401, reponse.text
    assert 'error="invalid_token"' in reponse.headers["www-authenticate"]


async def test_une_rotation_de_cles_est_suivie(idp: dict[str, Any], client: AsyncClient, admin: str) -> None:
    await _relier_admin()
    client.cookies.clear()
    assert (await _outils(client, _jeton(idp["k1"]))).status_code == 200
    idp["publiees"] = ["k1", "k2"]  # l'IdP publie une nouvelle clé, et signe avec elle
    assert (await _outils(client, _jeton(idp["k2"], kid="k2"))).status_code == 200


async def test_un_inconnu_de_choregos_est_refuse(
    idp: dict[str, Any], client: AsyncClient, admin: str
) -> None:
    await _relier_admin()
    client.cookies.clear()
    reponse = await _outils(client, _jeton(idp["k1"], sub="sub-jamais-vu"))
    assert reponse.status_code == 403, reponse.text


async def test_la_porte_d_un_projet_publie_son_propre_document(
    idp: dict[str, Any], client: AsyncClient, project: dict[str, Any]
) -> None:
    porte = "/mcp/projects/varga:billing-api"
    sans = await client.post(porte, json={"jsonrpc": "2.0", "id": 1, "method": "ping"})
    assert sans.status_code == 401
    document = f"/.well-known/oauth-protected-resource{porte}"
    assert f'{document}"' in sans.headers["www-authenticate"], "la ressource est l'URL saisie par le client"
    assert (await client.get(document)).json()["resource"].endswith(porte)


async def test_l_ecriture_se_demande_et_l_appel_est_audite_au_nom_du_client(
    idp: dict[str, Any], client: AsyncClient, project: dict[str, Any]
) -> None:
    reponse = await client.put(
        f"/api/v1/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert reponse.status_code == 200, reponse.text
    await _relier_admin()
    jeton = _jeton(idp["k1"], scope="openid mcp:write")
    reponse = await _outils(client, jeton)
    assert "create_work_item" in {o["name"] for o in reponse.json()["result"]["tools"]}
    cree = await client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "create_work_item",
                "arguments": {"project": "varga:billing-api", "title": "Par OAuth"},
            },
        },
        headers={"Authorization": f"Bearer {jeton}"},
    )
    assert cree.json()["result"]["isError"] is False, cree.text
    audit = (await client.get("/api/v1/audit")).json()["items"]
    (appel,) = [a for a in audit if a["action"] == "mcp.call" and a.get("target_id") == "create_work_item"]
    assert appel["payload"]["jeton"] == "oauth:claude-ai:sub-admin"


async def test_un_autre_idp_ne_retrouve_personne_par_son_sub(
    idp: dict[str, Any], client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    await _relier_admin()
    # La console se connecte ailleurs : le même `sub` ne désigne plus la même personne.
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", "https://console.test/realms/choregos")
    monkeypatch.setenv("CHOREGOS_MCP_OAUTH_ISSUER", EMETTEUR)
    reset_settings_cache()
    client.cookies.clear()
    assert (await _outils(client, _jeton(idp["k1"]))).status_code == 403
    non_verifie = _jeton(idp["k1"], email="admin@varga.dev", email_verified=False)
    assert (await _outils(client, non_verifie)).status_code == 403, "un e-mail non vérifié ne relie personne"
    verifie = _jeton(idp["k1"], email="admin@varga.dev", email_verified=True)
    assert (await _outils(client, verifie)).status_code == 200
