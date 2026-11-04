"""What the inventory of 2026-09-24 found open, and which must stay closed.

- the development login was on by default, never set by the chart, and `admin@whatever` became
  ORG_ADMIN on every organisation;
- an OIDC group gave its role on ALL the organisations of the instance;
- a role on `a/billing` held on `b/billing` (project roles indexed by slug alone);
- `state` served as a redirection target, with no nonce or PKCE;
- the API tokens never expired, and nothing could issue them.
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
#: The settings guard raises a French message; the test pins it as it is.
UNRESOLVED = "non résolus"  # lang: fr-ok


# ───────────────────────── development login ─────────────────────────


async def test_the_development_login_is_refused_in_production() -> None:
    from choregos_api.config import Settings

    with pytest.raises(ValueError, match="staging/prod"):
        Settings(env="prod", dev_login_enabled=True)
    assert Settings(env="prod", dev_login_enabled=False).dev_login_enabled is False


async def test_the_development_login_is_off_by_default() -> None:
    from choregos_api.config import Settings

    # The test process turns it on through the environment: it is the model's DEFAULT that is checked.
    assert Settings.model_fields["dev_login_enabled"].default is False


async def test_without_the_development_login_the_dev_code_is_refused(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.routers.auth import forget_discovery

    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    reset_settings_cache()
    forget_discovery()
    try:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
                return_value=Response(
                    200,
                    json={
                        "issuer": ISSUER,
                        "authorization_endpoint": f"{ISSUER}/authorize",
                        "token_endpoint": f"{ISSUER}/token",
                        "userinfo_endpoint": f"{ISSUER}/userinfo",
                    },
                )
            )
            start = await client.get("/api/v1/auth/login", params={"as": "admin@varga.dev"})
            assert start.status_code == 307
            # `?as=` ignored: we go to the IdP, with PKCE
            assert start.headers["location"].startswith(f"{ISSUER}/authorize?")
            assert "code_challenge_method=S256" in start.headers["location"]
            state = parse_qs(urlparse(start.headers["location"]).query)["state"][0]
            # the `dev:` branch is closed: the code goes to the IdP, which does not know it
            mock.post(f"{ISSUER}/token").mock(return_value=Response(400, json={"error": "invalid_grant"}))
            refused = await client.get(
                "/api/v1/auth/callback", params={"code": "dev:admin@varga.dev", "state": state}
            )
            assert refused.status_code == 401
    finally:
        reset_settings_cache()
        forget_discovery()


async def test_an_email_that_starts_with_admin_is_not_admin(client: AsyncClient, org: str) -> None:
    """Before: `admin*` => ORG_ADMIN. Now only the `dev_admin_emails` list counts."""
    await login(client, "admin@evil.dev")
    me = (await client.get("/api/v1/me")).json()
    assert [m["role"] for m in me["memberships"]] == ["developer"]
    created = await client.post(
        "/api/v1/orgs/varga/projects",
        json={"slug": "x", "name": "x", "config": {"slug": "x", "org": "varga"}},
    )
    assert created.status_code == 403


# ───────────────────────── roles per organisation ─────────────────────────


async def _second_organisation(project_slug: str = "billing-api") -> str:
    from choregos_api.db.models import Organization, Project
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        other = Organization(slug="other", name="Another organisation")
        session.add(other)
        await session.flush()
        project_row = Project(
            org_id=other.id,
            slug=project_slug,
            name="The same name, elsewhere",
            status="active",
            config={"slug": project_slug, "org": "other"},
        )
        session.add(project_row)
        await session.flush()
        return str(project_row.id)


async def test_a_project_role_does_not_cross_organisations(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`a/billing-api` and `other/billing-api`: a developer of the first does not read the second."""
    from choregos_api.config import reset_settings_cache

    # the development login gives a role only in the default organisation
    monkeypatch.setenv("CHOREGOS_OIDC_DEFAULT_ORG", "varga")
    reset_settings_cache()
    other_id = await _second_organisation()
    invited = await client.post(
        "/api/v1/orgs/varga/members",
        json={"email": "dev@varga.dev", "role": "developer", "project_slug": "billing-api"},
    )
    assert invited.status_code in {200, 201}, invited.text

    await login(client, "dev@varga.dev")
    me = (await client.get("/api/v1/me")).json()
    roles = {(m["org"], m.get("project_slug")): m["role"] for m in me["memberships"]}
    assert roles[("varga", "billing-api")] == "developer"
    assert ("other", "billing-api") not in roles

    assert (await client.get(f"/api/v1/projects/{project['id']}")).status_code == 200
    # 403 on SQLite (the route sees the project and refuses); 404 under PostgreSQL's RLS, which does
    # not even reveal that it exists: stricter, and what `test_rls_postgres.py` demands.
    refused_codes = {403, 404}
    assert (await client.get(f"/api/v1/projects/{other_id}")).status_code in refused_codes
    # `org:slug` in a URL segment (a `/` does not pass there); `org/slug` everywhere else
    assert (await client.get("/api/v1/projects/other:billing-api")).status_code in refused_codes
    # The slug alone: on SQLite the route sees both projects and refuses to guess (404 "ambiguous");
    # under PostgreSQL's RLS it sees only the caller's and returns it. In both cases, NEVER the
    # project of the other organisation.
    alone = await client.get("/api/v1/projects/billing-api")
    if alone.status_code == 200:
        assert alone.json()["id"] == project["id"], (
            "the slug alone returned the project of another organisation"
        )
    else:
        assert alone.status_code == 404 and "ambiguous" in alone.json()["detail"]
    assert (await client.get("/api/v1/projects/varga:billing-api")).status_code == 200
    reset_settings_cache()


async def test_the_oidc_groups_are_attached_to_their_organisation() -> None:
    from choregos_api.routers.auth import roles_from_groups
    from choregos_contracts import Role

    roles = roles_from_groups(
        ["choregos:varga:developers", "choregos:other:org-admins", "developers", "unrelated"],
        default_org="",
    )
    assert roles == {"varga": Role.DEVELOPER, "other": Role.ORG_ADMIN}
    # a bare group holds only for the default organisation, and the highest one wins
    roles = roles_from_groups(["developers", "choregos:varga:product-owners"], default_org="varga")
    assert roles == {"varga": Role.PROJECT_OWNER}


# ───────────────────────── OIDC: state, PKCE, redirection ─────────────────────────


def _discovery() -> dict[str, str]:
    return {
        "issuer": ISSUER,
        "authorization_endpoint": f"{ISSUER}/authorize",
        "token_endpoint": f"{ISSUER}/token",
        "userinfo_endpoint": f"{ISSUER}/userinfo",
    }


async def test_the_oidc_flow_checks_state_and_pkce_and_bounds_the_redirection(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.routers.auth import forget_discovery

    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    monkeypatch.setenv("CHOREGOS_OIDC_ISSUER", ISSUER)
    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", "http://front.test")
    reset_settings_cache()
    forget_discovery()
    seen: dict[str, Any] = {}

    def exchange(request: Any) -> Response:
        seen.update(parse_qs(request.content.decode()))
        return Response(200, json={"access_token": "at-1"})

    try:
        with respx.mock(assert_all_called=False) as mock:
            mock.get(f"{ISSUER}/.well-known/openid-configuration").mock(
                return_value=Response(200, json=_discovery())
            )
            mock.post(f"{ISSUER}/token").mock(side_effect=exchange)
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
            # 1. an outside target is refused from the start
            start = await client.get("/api/v1/auth/login", params={"redirect_to": "https://evil.test/phish"})
            query = parse_qs(urlparse(start.headers["location"]).query)
            state, challenge = query["state"][0], query["code_challenge"][0]

            # 2. a forged `state` is refused
            forged = await client.get("/api/v1/auth/callback", params={"code": "c", "state": state + "x"})
            assert forged.status_code == 401

            # 3. the real `state` passes, the code_verifier sent matches the challenge
            done = await client.get("/api/v1/auth/callback", params={"code": "c", "state": state})
            assert done.status_code == 307, done.text
            assert done.headers["location"] == "http://front.test"  # not evil.test
            verifier = seen["code_verifier"][0]
            expected = (
                base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
            )
            assert expected == challenge

            me = (await client.get("/api/v1/me")).json()
            assert me["email"] == "jo@varga.dev"
            assert {(m["org"], m["role"]) for m in me["memberships"]} == {("varga", "release_captain")}

            # 4. the same `state` does not serve twice: the handshake cookie is gone
            again = await client.get("/api/v1/auth/callback", params={"code": "c", "state": state})
            assert again.status_code == 401
    finally:
        reset_settings_cache()
        forget_discovery()


# ───────────────────────── API tokens ─────────────────────────


async def test_an_api_token_is_issued_used_and_revoked(client: AsyncClient, admin: str) -> None:
    from datetime import timedelta

    from choregos_api.db.models import ApiToken
    from choregos_api.db.session import session_scope
    from choregos_core import utcnow

    created = await client.post("/api/v1/me/tokens", json={"name": "ci", "expires_in_days": 1})
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["token"].startswith("chg_")
    assert body["expires_at"] is not None

    # the clear text serves as a bearer, without a cookie
    bearer = AsyncClient(transport=client._transport, base_url="http://test")
    me = await bearer.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert me.status_code == 200 and me.json()["email"] == admin

    listed = (await client.get("/api/v1/me/tokens")).json()
    assert [t["name"] for t in listed] == ["ci"]
    assert "token" not in listed[0]
    assert listed[0]["last_used_at"] is not None

    # expired => refused, even if the digest is known
    async with session_scope() as session:
        row = await session.get(ApiToken, body["id"])
        assert row is not None
        row.expires_at = utcnow() - timedelta(seconds=1)
    expired = await bearer.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    assert expired.status_code == 401
    assert "expired" in expired.json()["detail"]

    assert (await client.delete(f"/api/v1/me/tokens/{body['id']}")).status_code == 204
    assert (
        await bearer.get("/api/v1/me", headers={"Authorization": f"Bearer {body['token']}"})
    ).status_code == 401
    # a token is revoked only by its owner
    assert (await client.delete("/api/v1/me/tokens/unknown")).status_code == 404


# ───────────────────────── rate limiter, webhooks ─────────────────────────


def test_the_limiter_counts_per_address_and_sliding_window() -> None:
    from choregos_api.rate_limit import RateLimiter

    limiter = RateLimiter(per_minute=3)
    assert [limiter.admits("1.2.3.4", t)[0] for t in (0.0, 1.0, 2.0)] == [True, True, True]
    admitted, wait = limiter.admits("1.2.3.4", 3.0)
    assert not admitted and wait >= 1
    assert limiter.admits("5.6.7.8", 3.0)[0], "another address has its own window"
    assert limiter.admits("1.2.3.4", 61.0)[0], "the window slides: sixty seconds later, a place"
    assert RateLimiter(per_minute=0).admits("x")[0], "0 disables"


async def test_the_routes_without_a_principal_are_limited(
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


async def test_a_github_webhook_without_a_secret_is_refused_in_production(
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
            refusal = await c.post(
                "/api/v1/webhooks/github", content=b"{}", headers={"X-GitHub-Event": "ping"}
            )
        assert refusal.status_code == 401
        assert "not configured" in refusal.json()["detail"]
    finally:
        reset_settings_cache()


async def test_a_direct_gateway_is_not_written_in_production(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`gateway: direct` measures no cost and applies no ceiling.

    In development it is a legitimate choice: the agent brings its subscription. On a serious
    environment, it is accounting switched off without anyone having decided it: the bench ran
    like this for a week and "proved" a spend ceiling that had never measured any spend. So we
    refuse to write it, at the moment someone chooses it.
    """
    from choregos_api.config import get_settings, reset_settings_cache

    path = f"/api/v1/projects/{project['id']}/connectors/gateway"
    accepted = await client.put(path, json={"type": "direct", "config": {}})
    assert accepted.status_code == 200, accepted.text  # in a test, it is allowed

    monkeypatch.setenv("CHOREGOS_ENV", "prod")
    # the development login is refused in prod (guard of `config.py`): the cookie already set
    # is enough, we only switch the flag off so that the settings can be built
    monkeypatch.setenv("CHOREGOS_DEV_LOGIN_ENABLED", "false")
    reset_settings_cache()
    try:
        assert get_settings().env == "prod"
        refusal = await client.put(path, json={"type": "direct", "config": {}})
        assert refusal.status_code == 422, refusal.text
        assert "litellm" in refusal.json()["detail"]
        # and the gateway that measures does pass
        assert (await client.put(path, json={"type": "litellm", "config": {}})).status_code == 200
    finally:
        monkeypatch.delenv("CHOREGOS_ENV", raising=False)
        monkeypatch.delenv("CHOREGOS_DEV_LOGIN_ENABLED", raising=False)
        reset_settings_cache()


@pytest.mark.parametrize("artifact", ("<no value>", "<nil>", "  <no value>  "))
def test_an_unresolved_setting_prevents_the_start_up(artifact: str) -> None:
    """Ten characters that look like a value, and are not.

    On 2026-09-26, on the dev tenant, `GATEWAY_MASTER_KEY` and `PLATFORM_LLM_KEY` were both the
    string `<no value>`. The pod was `1/1 Running`, the tenant looked healthy, and the gateway
    answered 401 on the first model call. Nothing said the key had never been resolved: it is the
    failure mode of any secret rendered by a Go template, Helm as well as the Infisical operator,
    and it hits the session secret just as well.

    The refusal holds in ALL environments: nobody writes `<no value>` on purpose.
    """
    from choregos_api.config import Settings

    with pytest.raises(ValueError, match=UNRESOLVED):
        Settings(session_secret=artifact)


def test_a_real_value_passes() -> None:
    """The guard must not refuse a secret that contains an angle bracket by chance."""
    from choregos_api.config import Settings

    assert Settings(session_secret="<s3cr3t-with-brackets>").session_secret
