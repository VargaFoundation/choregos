"""Une authentification FRAÎCHE l'est-elle vraiment ?

`authentifie_le` lisait l'`iat` de la session : l'heure où Choregos l'a ouverte. Après une
reconnexion SSO silencieuse — l'IdP a encore une session, il rend la main sans rien demander —
cette heure est neuve alors que l'utilisateur ne s'est pas authentifié depuis des heures. Une porte
qui exige une authentification récente (approuver une release, valider une action risquée) était
donc satisfaite par un simple aller-retour vers `/auth/login`.

Désormais la session porte `auth_time`, l'heure d'authentification que l'IdP écrit dans l'ID token,
et `reauth=1` EXIGE qu'elle soit récente : sans quoi l'IdP n'a pas honoré `prompt=login`.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import AsyncClient, Response

ISSUER = "https://idp.example.test/realms/x"


def _id_token(**revendications: Any) -> str:
    """Un ID token tel que le rend le point `token` (la signature n'est pas lue par l'API)."""

    def b64(valeur: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(valeur).encode()).decode().rstrip("=")

    return f"{b64({'alg': 'RS256'})}.{b64({'sub': 'u-1', **revendications})}.c2lnbmF0dXJl"


@pytest.fixture
def idp(org: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    from choregos_api.config import reset_settings_cache
    from choregos_api.routers.auth import oublier_la_decouverte

    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    reset_settings_cache()
    oublier_la_decouverte()
    jetons: dict[str, Any] = {"access_token": "at-1"}
    with respx.mock(assert_all_called=False) as mock:
        mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=Response(
                200,
                json={
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "userinfo_endpoint": f"{ISSUER}/userinfo",
                },
            )
        )
        mock.post(f"{ISSUER}/token").mock(side_effect=lambda _: Response(200, json=jetons))
        mock.get(f"{ISSUER}/userinfo").mock(
            return_value=Response(200, json={"sub": "u-1", "email": "jo@varga.dev", "name": "Jo"})
        )
        yield jetons
    reset_settings_cache()
    oublier_la_decouverte()


async def _connexion(client: AsyncClient, *, reauth: bool) -> Response:
    debut = await client.get("/api/v1/auth/login", params={"reauth": "1"} if reauth else {})
    parametres = parse_qs(urlparse(debut.headers["location"]).query)
    assert ("prompt" in parametres) is reauth
    return await client.get("/api/v1/auth/callback", params={"code": "c", "state": parametres["state"][0]})


def _session(client: AsyncClient) -> dict[str, Any]:
    from choregos_api.security import read_session

    charge = read_session(client.cookies.get("choregos_session", ""))
    assert charge is not None, "aucune session ouverte"
    return charge


async def _authentifie_le(client: AsyncClient) -> int | None:
    """Ce que voit une porte : `Principal.authentifie_le`, résolu par la vraie dépendance."""
    from choregos_api.config import get_settings
    from choregos_api.db.session import session_scope
    from choregos_api.deps import current_principal
    from starlette.requests import Request

    cookie = f"choregos_session={client.cookies.get('choregos_session')}"
    requete = Request({"type": "http", "headers": [(b"cookie", cookie.encode())], "query_string": b""})
    async with session_scope() as session:
        principal = await current_principal(requete, session, get_settings(), None)
    return principal.authentifie_le


async def test_une_reconnexion_sso_garde_l_heure_d_authentification_de_l_idp(
    client: AsyncClient, idp: dict[str, Any]
) -> None:
    il_y_a_une_heure = int(time.time()) - 3600
    idp["id_token"] = _id_token(auth_time=il_y_a_une_heure)
    reponse = await _connexion(client, reauth=False)
    assert reponse.status_code == 307, reponse.text
    session = _session(client)
    assert session["iat"] > il_y_a_une_heure + 3000, "la session est neuve…"
    assert session["auth_time"] == il_y_a_une_heure, "…l'authentification ne l'est pas"
    assert await _authentifie_le(client) == il_y_a_une_heure


async def test_reauth_refuse_un_idp_qui_n_a_pas_redemande(client: AsyncClient, idp: dict[str, Any]) -> None:
    idp["id_token"] = _id_token(auth_time=int(time.time()) - 3600)
    refus = await _connexion(client, reauth=True)
    assert refus.status_code == 401, refus.text
    assert "prompt=login" in refus.text
    assert not client.cookies.get("choregos_session")


async def test_reauth_sans_auth_time_est_refuse(client: AsyncClient, idp: dict[str, Any]) -> None:
    refus = await _connexion(client, reauth=True)
    assert refus.status_code == 401, refus.text


async def test_reauth_fraiche_ouvre_une_session_qui_le_dit(client: AsyncClient, idp: dict[str, Any]) -> None:
    maintenant = int(time.time())
    idp["id_token"] = _id_token(auth_time=maintenant)
    reponse = await _connexion(client, reauth=True)
    assert reponse.status_code == 307, reponse.text
    assert _session(client)["auth_time"] == maintenant
    assert await _authentifie_le(client) == maintenant


async def test_sans_auth_time_l_heure_reste_celle_de_la_session(
    client: AsyncClient, idp: dict[str, Any]
) -> None:
    """Un IdP qui ne dit rien garde l'ancien comportement : `iat`, faute de mieux."""
    reponse = await _connexion(client, reauth=False)
    assert reponse.status_code == 307, reponse.text
    session = _session(client)
    assert "auth_time" not in session
    assert await _authentifie_le(client) == session["iat"]
