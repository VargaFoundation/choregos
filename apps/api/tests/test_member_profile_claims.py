"""What the identity provider says of a person is stored at each sign-in (S66-42, ADR 0162).

`given_name` and `family_name` (standard OIDC), a job title and a department when the provider sends them:
replaced by each sign-in that carries them, kept by one that does not. A `picture` claim is never fetched
(no outbound call to an address a provider chose) and never stored.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterator
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from choregos_api.config import reset_settings_cache
from choregos_api.db.models import User
from choregos_api.db.session import session_scope
from choregos_api.routers.auth import forget_discovery
from httpx import AsyncClient, Response
from sqlalchemy import select

ISSUER = "https://idp.example.test/realms/x"
DISCOVERY = {
    "issuer": ISSUER,
    "authorization_endpoint": f"{ISSUER}/authorize",
    "token_endpoint": f"{ISSUER}/token",
    "userinfo_endpoint": f"{ISSUER}/userinfo",
}


def _id_token(**claims: Any) -> str:
    def b64(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip("=")

    body = {"iss": ISSUER, "aud": "choregos"} | claims
    return f"{b64({'alg': 'RS256'})}.{b64(body)}.c2ln"


@pytest.fixture(autouse=True)
def _provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    reset_settings_cache()
    forget_discovery()
    yield
    reset_settings_cache()
    forget_discovery()


async def _sign_in(client: AsyncClient, userinfo: dict[str, Any]) -> Response:
    """A whole sign-in. respx refuses any request it does not mock: a fetched `picture` would fail it."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as mock:
        mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
            return_value=Response(200, json=DISCOVERY)
        )
        start = await client.get("/api/v1/auth/login")
        params = parse_qs(urlparse(start.headers["location"]).query)
        tokens = {"access_token": "at", "id_token": _id_token(sub=userinfo["sub"], nonce=params["nonce"][0])}
        mock.post(f"{ISSUER}/token").mock(return_value=Response(200, json=tokens))
        mock.get(f"{ISSUER}/userinfo").mock(return_value=Response(200, json=userinfo))
        return await client.get("/api/v1/auth/callback", params={"code": "c", "state": params["state"][0]})


async def _user(email: str) -> User:
    async with session_scope(orgs="*") as session:
        return (await session.execute(select(User).where(User.email == email))).scalar_one()


async def test_the_standard_claims_and_the_job_title_are_stored_at_sign_in(
    client: AsyncClient, org: str
) -> None:
    done = await _sign_in(
        client,
        {
            "sub": "u-1",
            "email": "ada@varga.dev",
            "name": "Ada Lovelace",
            "given_name": "Ada",
            "family_name": "Lovelace",
            "jobTitle": "Analyst",
            "department": "Engines",
            "picture": "https://cdn.example.test/ada.png",
        },
    )
    assert done.status_code == 307, done.text
    ada = await _user("ada@varga.dev")
    assert (ada.given_name, ada.family_name, ada.job_title, ada.department) == (
        "Ada",
        "Lovelace",
        "Analyst",
        "Engines",
    )
    me = (await client.get("/api/v1/me")).json()
    assert me["given_name"] == "Ada" and me["job_title"] == "Analyst" and me["avatar_url"] is None


async def test_a_later_sign_in_replaces_what_it_carries_and_keeps_the_rest(
    client: AsyncClient, org: str
) -> None:
    first = {"sub": "u-2", "email": "bob@varga.dev", "given_name": "Bob", "job_title": "Engineer"}
    assert (await _sign_in(client, first)).status_code == 307
    later = {"sub": "u-2", "email": "bob@varga.dev", "given_name": "Robert", "family_name": "Smith"}
    assert (await _sign_in(client, later)).status_code == 307
    bob = await _user("bob@varga.dev")
    assert (bob.given_name, bob.family_name, bob.job_title) == ("Robert", "Smith", "Engineer")


async def test_an_extension_sign_in_stores_the_profile_it_maps(client: AsyncClient, org: str) -> None:
    """SAML, brought by an extension, maps its attributes onto the same columns through the public finish."""
    from choregos_api.routers.auth import terminer_la_connexion

    async with session_scope(orgs="*") as session:
        await terminer_la_connexion(
            session,
            email="cy@varga.dev",
            nom="Cy",
            sujet="saml-1",
            groupes=[],
            cible="/",
            canal="saml",
            profile={"given_name": "Cy", "family_name": "Young", "department": "Ops", "picture": "x"},
        )
    cy = await _user("cy@varga.dev")
    assert (cy.given_name, cy.family_name, cy.job_title, cy.department) == ("Cy", "Young", None, "Ops")


def test_a_tenant_provider_names_its_own_job_title_and_department_claims() -> None:
    from choregos_api.routers.auth import provider_profile
    from choregos_api.tenant_sign_in import OidcProvider

    provider = OidcProvider(
        issuer="https://idp.acme.example", client_id="c", job_title_claim="title", department_claim="ou"
    )
    info = {"given_name": " Ann ", "title": "Lead", "ou": "R&D", "job_title": "ignored", "family_name": ""}
    assert provider_profile(info, provider) == {"given_name": "Ann", "job_title": "Lead", "department": "R&D"}
    assert provider_profile({"job_title": "x" * 300})["job_title"] == "x" * 200
