# SPDX-License-Identifier: Apache-2.0
"""Les coutures d'identité : révoquer une session, désigner un administrateur de plateforme,
exiger une authentification fraîche — chacune éprouvée par un greffon INSTALLÉ.

Le greffon est un vrai module avec son `.dist-info` ; `create_app()` le charge comme au démarrage.
"""

from __future__ import annotations

import os
import pathlib
import sys
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

GREFFON = """
from choregos_api.edition import (
    SessionRefusee,
    declarer_un_administrateur_de_plateforme,
    declarer_une_validation_de_session,
)

#: Réglé par le test.
ETAT = {"revoques": set(), "vus": [], "admins": set(), "panne": False}


async def pas_revoquee(session, user, payload):
    ETAT["vus"].append(dict(payload))
    if ETAT["panne"]:
        raise ConnectionError("table des révocations injoignable")
    if user.email in ETAT["revoques"]:
        raise SessionRefusee("session révoquée par l'administrateur : se reconnecter")


def administrateur_de_plateforme(session, principal):
    return principal.email in ETAT["admins"]


def brancher():
    declarer_une_validation_de_session(pas_revoquee)
    declarer_un_administrateur_de_plateforme(administrateur_de_plateforme)
"""


@pytest.fixture
def greffon(tmp_path: pathlib.Path) -> Iterator[Any]:
    from choregos_api.edition import reinitialiser

    racine = tmp_path / "site"
    racine.mkdir()
    (racine / "greffon_identite.py").write_text(GREFFON, encoding="utf-8")
    info = racine / "greffon_identite-0.1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: greffon_identite\nVersion: 0.1.0\n", encoding="utf-8"
    )
    (info / "entry_points.txt").write_text(
        "[choregos.plugins]\ngreffon_identite = greffon_identite:brancher\n", encoding="utf-8"
    )
    sys.path.insert(0, str(racine))
    try:
        yield racine
    finally:
        sys.path.remove(str(racine))
        sys.modules.pop("greffon_identite", None)
        reinitialiser()


@pytest.fixture
async def client_greffe(greffon: pathlib.Path, tmp_path: pathlib.Path) -> AsyncIterator[AsyncClient]:
    os.environ["CHOREGOS_DATABASE_URL"] = f"sqlite+aiosqlite:///{tmp_path}/identite.db"
    from choregos_api.config import reset_settings_cache
    from choregos_api.db import session as db_session
    from choregos_api.db.models import Organization
    from choregos_api.db.session import create_all, session_scope
    from choregos_api.main import create_app

    reset_settings_cache()
    await db_session.dispose_engine()
    application = create_app()
    await create_all()
    async with session_scope() as session:
        session.add(Organization(slug="varga", name="Varga"))
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as http:
        yield http
    await db_session.dispose_engine()
    reset_settings_cache()


def _etat() -> dict[str, Any]:
    return sys.modules["greffon_identite"].ETAT  # type: ignore[no-any-return]


async def _connecter(client: AsyncClient, email: str) -> None:
    client.cookies.clear()
    depart = await client.get("/api/v1/auth/login", params={"as": email})
    await client.get(depart.headers["location"])


async def test_la_session_porte_son_heure_d_authentification(client_greffe: AsyncClient) -> None:
    avant = int(time.time())
    await _connecter(client_greffe, "admin@varga.dev")
    assert (await client_greffe.get("/api/v1/me")).status_code == 200
    (payload,) = _etat()["vus"]
    assert avant <= payload["iat"] <= int(time.time())


async def test_une_session_revoquee_est_refusee_a_la_requete_suivante(client_greffe: AsyncClient) -> None:
    await _connecter(client_greffe, "admin@varga.dev")
    assert (await client_greffe.get("/api/v1/me")).status_code == 200
    _etat()["revoques"].add("admin@varga.dev")
    refus = await client_greffe.get("/api/v1/me")
    assert refus.status_code == 401
    assert "session révoquée par l'administrateur" in refus.text


async def test_une_validation_en_panne_ne_vaut_pas_acceptation(client_greffe: AsyncClient) -> None:
    await _connecter(client_greffe, "admin@varga.dev")
    _etat()["panne"] = True
    with pytest.raises(ConnectionError):
        await client_greffe.get("/api/v1/me")


async def test_un_greffon_accorde_l_administration_de_la_plateforme_sans_la_retirer(
    client_greffe: AsyncClient,
) -> None:
    """Deux organisations : la règle du cœur (admin de TOUTES) refuse le chef de `varga` seul ; le
    greffon peut l'accorder à quelqu'un d'autre, sans retirer le droit à qui le cœur le donne."""
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    await _connecter(client_greffe, "admin@varga.dev")
    # Une seconde organisation, posée en base : l'édition communautaire refuse de la créer par l'API.
    async with session_scope(orgs="*") as session:
        seconde = Organization(slug="seconde", name="S")
        session.add(seconde)
        await session.flush()
        admin = (await session.execute(select(User).where(User.email == "admin@varga.dev"))).scalar_one()
        session.add(Membership(user_id=admin.id, org_id=seconde.id, role="org_admin"))
    # L'admin administre les deux : le cœur le lui accorde, greffon ou pas.
    assert (await client_greffe.get("/api/v1/platform/gateway/keys")).status_code == 200

    await _connecter(client_greffe, "ops@varga.dev")
    assert (await client_greffe.get("/api/v1/platform/gateway/keys")).status_code == 403
    _etat()["admins"].add("ops@varga.dev")
    assert (await client_greffe.get("/api/v1/platform/gateway/keys")).status_code == 200


ISSUER = "https://idp.example.test/realms/x"


async def test_reauth_demande_a_l_idp_de_re_authentifier(
    client_greffe: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.routers.auth import oublier_la_decouverte

    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    reset_settings_cache()
    oublier_la_decouverte()
    try:
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
            ordinaire = parse_qs(
                urlparse((await client_greffe.get("/api/v1/auth/login")).headers["location"]).query
            )
            assert "prompt" not in ordinaire and "max_age" not in ordinaire
            frais = await client_greffe.get("/api/v1/auth/login", params={"reauth": "1"})
            query = parse_qs(urlparse(frais.headers["location"]).query)
            assert query["prompt"] == ["login"] and query["max_age"] == ["0"]
    finally:
        reset_settings_cache()
        oublier_la_decouverte()
