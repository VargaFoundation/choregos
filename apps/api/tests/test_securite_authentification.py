"""Ce que l'état des lieux du 2026-09-24 a trouvé ouvert, et qui doit rester fermé.

- la connexion de développement était allumée par défaut, jamais posée par le chart, et
  `admin@n-importe-quoi` devenait ORG_ADMIN sur toutes les organisations ;
- un groupe OIDC donnait son rôle sur TOUTES les organisations de l'instance ;
- un rôle sur `a/billing` valait sur `b/billing` (rôles de projet indexés par slug seul) ;
- `state` servait de cible de redirection, sans nonce ni PKCE ;
- les jetons d'API n'expiraient jamais, et rien ne pouvait en émettre.
"""

from __future__ import annotations

import base64
import hashlib
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import ASGITransport, AsyncClient, Response

from .conftest import login

ISSUER = "https://idp.example.test/realms/x"


# ───────────────────────── connexion de développement ─────────────────────────


async def test_la_connexion_de_developpement_est_refusee_en_production() -> None:
    from choregos_api.config import Settings

    with pytest.raises(ValueError, match="staging/prod"):
        Settings(env="prod", dev_login_enabled=True)
    assert Settings(env="prod", dev_login_enabled=False).dev_login_enabled is False


async def test_la_connexion_de_developpement_est_eteinte_par_defaut() -> None:
    from choregos_api.config import Settings

    # Le processus de test l'allume par l'environnement : c'est le DÉFAUT du modèle qu'on vérifie.
    assert Settings.model_fields["dev_login_enabled"].default is False


async def test_sans_connexion_de_developpement_le_code_dev_est_refuse(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
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
            start = await client.get("/api/v1/auth/login", params={"as": "admin@varga.dev"})
            assert start.status_code == 307
            # `?as=` ignoré : on part vers l'IdP, avec PKCE
            assert start.headers["location"].startswith(f"{ISSUER}/authorize?")
            assert "code_challenge_method=S256" in start.headers["location"]
            state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
            # la branche `dev:` est fermée : le code part à l'IdP, qui ne le connaît pas
            mock.post(f"{ISSUER}/token").mock(return_value=Response(400, json={"error": "invalid_grant"}))
            refused = await client.get(
                "/api/v1/auth/callback", params={"code": "dev:admin@varga.dev", "state": state}
            )
            assert refused.status_code == 401
    finally:
        reset_settings_cache()
        oublier_la_decouverte()


async def test_un_email_qui_commence_par_admin_n_est_pas_admin(client: AsyncClient, org: str) -> None:
    """Avant : `admin*` ⇒ ORG_ADMIN. Désormais seule la liste `dev_admin_emails` compte."""
    await login(client, "admin@evil.dev")
    me = (await client.get("/api/v1/me")).json()
    assert [m["role"] for m in me["memberships"]] == ["developer"]
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "x", "name": "x", "config": {"slug": "x", "org": "varga"}},
    )
    assert created.status_code == 403


# ───────────────────────── rôles par organisation ─────────────────────────


async def _seconde_organisation(slug_projet: str = "billing-api") -> str:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        autre = Organization(slug="autre", name="Une autre organisation")
        session.add(autre)
        await session.flush()
        projet = Project(
            org_id=autre.id,
            slug=slug_projet,
            name="Le même nom, ailleurs",
            status="active",
            config={"slug": slug_projet, "org": "autre"},
        )
        session.add(projet)
        await session.flush()
        return str(projet.id)


async def test_un_role_de_projet_ne_traverse_pas_les_organisations(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`a/billing-api` et `autre/billing-api` : un développeur du premier ne lit pas le second."""
    from choregos_api.config import reset_settings_cache

    # la connexion de développement ne donne un rôle que dans l'organisation par défaut
    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", "varga")
    reset_settings_cache()
    autre_id = await _seconde_organisation()
    invited = await client.post(
        "/api/v1/orgs/varga/members",
        json={"email": "dev@varga.dev", "role": "developer", "project_slug": "billing-api"},
    )
    assert invited.status_code in {200, 201}, invited.text

    await login(client, "dev@varga.dev")
    me = (await client.get("/api/v1/me")).json()
    roles = {(m["org"], m.get("project_slug")): m["role"] for m in me["memberships"]}
    assert roles[("varga", "billing-api")] == "developer"
    assert ("autre", "billing-api") not in roles

    assert (await client.get(f"/api/v1/projects/{project['id']}")).status_code == 200
    assert (await client.get(f"/api/v1/projects/{autre_id}")).status_code == 403
    # `org:slug` dans un segment d'URL (un `/` n'y passe pas) ; `org/slug` partout ailleurs
    assert (await client.get("/api/v1/projects/autre:billing-api")).status_code == 403
    # le slug seul est ambigu entre deux organisations : on le dit, on ne devine pas
    ambigu = await client.get("/api/v1/projects/billing-api")
    assert ambigu.status_code == 404
    assert "ambigu" in ambigu.json()["detail"]
    assert (await client.get("/api/v1/projects/varga:billing-api")).status_code == 200
    reset_settings_cache()


async def test_les_groupes_oidc_sont_rattaches_a_leur_organisation() -> None:
    from choregos_api.routers.auth import roles_des_groupes
    from choregos_contracts import Role

    roles = roles_des_groupes(
        ["choregos:varga:developers", "choregos:autre:org-admins", "developers", "sans-rapport"],
        default_org="",
    )
    assert roles == {"varga": Role.DEVELOPER, "autre": Role.ORG_ADMIN}
    # un groupe nu ne vaut que pour l'organisation par défaut, et le plus élevé gagne
    roles = roles_des_groupes(["developers", "choregos:varga:product-owners"], default_org="varga")
    assert roles == {"varga": Role.PROJECT_OWNER}


# ───────────────────────── OIDC : state, PKCE, redirection ─────────────────────────


def _discovery() -> dict[str, str]:
    return {
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
    }


async def test_le_flux_oidc_verifie_state_et_pkce_et_borne_la_redirection(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.routers.auth import oublier_la_decouverte

    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", "http://front.test")
    reset_settings_cache()
    oublier_la_decouverte()
    vu: dict[str, Any] = {}

    def echange(request: Any) -> Response:
        vu.update(parse_qs(request.content.decode()))
        return Response(200, json={"access_token": "at-1"})

    try:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
                return_value=Response(200, json=_discovery())
            )
            mock.post(f"{ISSUER}/token").mock(side_effect=echange)
            mock.get(f"{ISSUER}/userinfo").mock(
                return_value=Response(
                    200,
                    json={
                        "sub": "u-1",
                        "email": "jo@varga.dev",
                        "name": "Jo",
                        "groups": ["choregos:varga:release-captains"],
                    },
                )
            )
            # 1. une cible extérieure est refusée dès le départ
            start = await client.get("/api/v1/auth/login", params={"redirect_to": "https://evil.test/phish"})
            query = parse_qs(urlparse(start.headers["location"]).query)
            state, challenge = query["state"][0], query["code_challenge"][0]

            # 2. un `state` forgé est refusé
            forged = await client.get("/api/v1/auth/callback", params={"code": "c", "state": state + "x"})
            assert forged.status_code == 401

            # 3. le vrai `state` passe, le code_verifier envoyé correspond au challenge
            done = await client.get("/api/v1/auth/callback", params={"code": "c", "state": state})
            assert done.status_code == 307, done.text
            assert done.headers["location"] == "http://front.test"  # pas evil.test
            verifier = vu["code_verifier"][0]
            attendu = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            )
            assert attendu == challenge

            me = (await client.get("/api/v1/me")).json()
            assert me["email"] == "jo@varga.dev"
            assert {(m["org"], m["role"]) for m in me["memberships"]} == {("varga", "release_captain")}

            # 4. le même `state` ne sert pas deux fois : le cookie de poignée de main est parti
            again = await client.get("/api/v1/auth/callback", params={"code": "c", "state": state})
            assert again.status_code == 401
    finally:
        reset_settings_cache()
        oublier_la_decouverte()


# ───────────────────────── jetons d'API ─────────────────────────


async def test_un_jeton_d_api_s_emet_s_use_et_se_revoque(client: AsyncClient, admin: str) -> None:
    from datetime import timedelta

    from choregos_api.db.models import ApiToken
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    created = await client.post("/api/v1/me/tokens", json={"name": "ci", "expires_in_days": 1})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token"].startswith("chg_")
    assert body["expires_at"] is not None

    # le clair sert de porteur, sans cookie
    porteur = AsyncClient(transport=client._transport, base_url="http://test")
    me = await porteur.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200 and me.json()["email"] == admin

    listed = (await client.get("/api/v1/me/tokens")).json()
    assert [t["name"] for t in listed] == ["ci"]
    assert "token" not in listed[0]
    assert listed[0]["last_used_at"] is not None

    # expiré ⇒ refusé, même si l'empreinte est connue
    async with session_scope() as session:
        row = await session.get(ApiToken, body["id"])
        assert row is not None
        row.expires_at = utcnow() - timedelta(seconds=1)
    expired = await porteur.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert expired.status_code == 401
    assert "expiré" in expired.json()["detail"]

    assert (await client.delete(f"/api/v1/me/tokens/{body['id']}")).status_code == 204
    assert (
        await porteur.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    ).status_code == 401
    # un jeton ne se révoque que par son propriétaire
    assert (await client.delete("/api/v1/me/tokens/inconnu")).status_code == 404


# ───────────────────────── limiteur, webhooks ─────────────────────────


def test_le_limiteur_compte_par_adresse_et_fenetre_glissante() -> None:
    from choregos_api.limiteur import Limiteur

    limiteur = Limiteur(par_minute=3)
    assert [limiteur.admet("1.2.3.4", t)[0] for t in (0.0, 1.0, 2.0)] == [True, True, True]
    admis, attente = limiteur.admet("1.2.3.4", 3.0)
    assert not admis and attente >= 1
    assert limiteur.admet("5.6.7.8", 3.0)[0], "une autre adresse a sa propre fenêtre"
    assert limiteur.admet("1.2.3.4", 61.0)[0], "la fenêtre glisse : soixante secondes plus tard, une place"
    assert Limiteur(par_minute=0).admet("x")[0], "0 désactive"


async def test_les_routes_sans_principal_sont_limitees(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_RATE_LIMIT_PER_MINUTE", "2")
    from choregos_api.config import reset_settings_cache

    reset_settings_cache()
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            codes = [(await c.post("/api/v1/webhooks/jira", json={})).status_code for _ in range(3)]
        assert codes[:2] != [429, 429] and codes[2] == 429
    finally:
        reset_settings_cache()


async def test_un_webhook_github_sans_secret_est_refuse_en_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_ENV", "prod")
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_GITHUB_WEBHOOK_SECRET", "")
    reset_settings_cache()
    try:
        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            refus = await c.post("/api/v1/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "ping"})
        assert refus.status_code == 401
        assert "non configuré" in refus.json()["detail"]
    finally:
        reset_settings_cache()


async def test_une_passerelle_directe_ne_s_ecrit_pas_en_production(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gateway: direct` ne mesure aucun coût et n'applique aucun plafond.

    En développement c'est un choix légitime — l'agent apporte son abonnement. Sur un
    environnement sérieux, c'est une comptabilité éteinte sans que personne l'ait décidé : le
    banc a tourné ainsi une semaine et « prouvait » un plafond de dépense qui n'avait jamais
    mesuré une dépense. On refuse donc de l'écrire, au moment où quelqu'un la choisit.
    """
    from choregos_api.config import get_settings, reset_settings_cache

    chemin = f"/api/v1/projects/{project['id']}/connectors/gateway"
    accepte = await client.put(chemin, json={"type": "direct", "config": {}})
    assert accepte.status_code == 200, accepte.text  # en test, c'est permis

    monkeypatch.setenv("CHOREGOS_ENV", "prod")
    # la connexion de développement est refusée en prod (garde de `config.py`) : le cookie déjà
    # posé suffit, on éteint seulement le drapeau pour que les réglages se construisent
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    reset_settings_cache()
    try:
        assert get_settings().env == "prod"
        refus = await client.put(chemin, json={"type": "direct", "config": {}})
        assert refus.status_code == 422, refus.text
        assert "litellm" in refus.json()["detail"]
        # et la passerelle qui compte, elle, passe
        assert (await client.put(chemin, json={"type": "litellm", "config": {}})).status_code == 200
    finally:
        monkeypatch.delenv("CHOREGOS_ENV", raising=False)
        monkeypatch.delenv("CHOREGOS_DEV_LOGIN_ENABLED", raising=False)
        reset_settings_cache()
