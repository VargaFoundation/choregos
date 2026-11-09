"""The GitHub App and its client: short, targeted tokens, quotas respected, named errors."""

from __future__ import annotations

import time

import httpx
import jwt
import pytest
from choregos_adapters.errors import UpstreamError
from choregos_adapters.github import client as client_module
from choregos_adapters.github.auth import RUNNER_PERMISSIONS, GitHubAppAuth
from choregos_adapters.github.client import GitHubClient
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ._transport import Wire, no_wait

API = "https://api.github.test"


@pytest.fixture(scope="module")
def private_key() -> tuple[str, str]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()
    pub = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    return pem, pub


def test_the_app_jwt_is_short_and_signed_by_the_key(private_key: tuple[str, str]) -> None:
    pem, pub = private_key
    token = GitHubAppAuth(app_id="12345", private_key=pem).app_jwt(ttl_s=540)
    claims = jwt.decode(token, pub, algorithms=["RS256"])
    assert claims["iss"] == "12345"
    assert claims["exp"] - claims["iat"] == 600, "540 s of life + 60 s of clock drift"


async def test_the_installation_token_targets_one_repository_and_is_cached(
    private_key: tuple[str, str],
) -> None:
    pem, _ = private_key
    wire = Wire(
        {
            ("GET", "/repos/varga/billing/installation"): (200, {"id": 42}),
            ("POST", "/app/installations/42/access_tokens"): (201, {"token": "ghs_short"}),
        }
    )
    auth = GitHubAppAuth(app_id="1", private_key=pem, base_url=API)
    async with wire.client() as client:
        token = await auth.token_for(client, "varga/billing", permissions=RUNNER_PERMISSIONS, ttl_s=900)
        again = await auth.token_for(client, "varga/billing", permissions=RUNNER_PERMISSIONS)
    assert token.token == "ghs_short" and again is token, "same repository, same permissions: the cache"
    request_body = wire.body(-1)
    assert request_body == {"repositories": ["billing"], "permissions": RUNNER_PERMISSIONS}, (
        "never a broad token: the exact repository and permissions"
    )
    assert wire.requests[1].headers["Authorization"].startswith("Bearer ey")
    assert not token.expired and token.repositories == ("varga/billing",)


async def test_an_expired_token_is_requested_again(private_key: tuple[str, str]) -> None:
    pem, _ = private_key
    wire = Wire(
        {("POST", "/app/installations/7/access_tokens"): [(201, {"token": "a"}), (201, {"token": "b"})]}
    )
    auth = GitHubAppAuth(app_id="1", private_key=pem, base_url=API)
    async with wire.client() as client:
        first = await auth.token_for(client, "varga/x", installation_id=7)
        first.expires_at = time.time()  # within the safety margin: stale
        second = await auth.token_for(client, "varga/x", installation_id=7)
    assert (first.token, second.token) == ("a", "b")


async def test_a_token_asks_for_statuses_and_does_without_when_the_app_lacks_them(
    private_key: tuple[str, str],
) -> None:
    """S59-10: the adapters' token asks for `statuses: read` (a Tekton CI's commit statuses). An App
    installed before it was asked for refuses the whole token (422): it is asked again without it."""
    from choregos_adapters.github.auth import DEFAULT_PERMISSIONS

    pem, _ = private_key
    assert DEFAULT_PERMISSIONS["statuses"] == "read"
    wire = Wire(
        {
            ("POST", "/app/installations/7/access_tokens"): [
                (422, {"message": "The permissions requested are not granted to this installation."}),
                (201, {"token": "ghs_without_statuses"}),
            ]
        }
    )
    auth = GitHubAppAuth(app_id="1", private_key=pem, base_url=API)
    async with wire.client() as client:
        token = await auth.token_for(client, "varga/x", installation_id=7, permissions=DEFAULT_PERMISSIONS)
    assert token.token == "ghs_without_statuses"
    assert "statuses" in wire.body(0)["permissions"]
    assert "statuses" not in wire.body(1)["permissions"], "asked again without the optional permission"


async def test_the_client_replays_secondary_limits_and_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    waits = no_wait(monkeypatch, client_module)
    wire = Wire(
        {
            ("GET", "/repos/varga/x"): [
                httpx.Response(403, text="You have exceeded a secondary rate limit"),
                httpx.Response(502, text="bad gateway"),
                httpx.Response(429, headers={"retry-after": "3"}, text=""),
                (200, {"full_name": "varga/x"}),
            ]
        }
    )
    client = GitHubClient(token="t", base_url=API, client=wire.client())
    assert await client.request("GET", "/repos/varga/x", repo="varga/x") == {"full_name": "varga/x"}
    assert len(waits) == 3 and waits[2] == 3.0
    assert wire.requests[0].headers["X-GitHub-Api-Version"] == "2022-11-28"


async def test_an_ordinary_403_is_an_error_with_the_reset_delay() -> None:
    reset = int(time.time()) + 120
    wire = Wire(
        {
            ("GET", "/repos/varga/x"): httpx.Response(
                403, headers={"x-ratelimit-reset": str(reset)}, text="Resource not accessible by integration"
            )
        }
    )
    client = GitHubClient(token="t", base_url=API, client=wire.client())
    with pytest.raises(UpstreamError) as exc:
        await client.request("GET", "/repos/varga/x")
    assert (
        exc.value.status_code == 403
        and exc.value.retry_after is not None
        and 100 <= exc.value.retry_after <= 120
    )
    assert len(wire.requests) == 1


async def test_without_authentication_the_client_refuses_before_calling() -> None:
    wire = Wire()
    with pytest.raises(UpstreamError, match="no authentication"):
        await GitHubClient(base_url=API, client=wire.client()).request("GET", "/x")
    assert wire.requests == []


async def test_graphql_raises_the_errors_of_the_body_even_on_a_200() -> None:
    wire = Wire(
        {
            ("POST", "/graphql"): [
                (200, {"errors": [{"message": "Could not resolve to a node"}]}),
                (200, {"data": {"node": {"id": "PVT_1"}}}),
            ]
        }
    )
    client = GitHubClient(token="t", base_url=API, graphql_url=f"{API}/graphql", client=wire.client())
    with pytest.raises(UpstreamError, match="Could not resolve"):
        await client.graphql("query { x }", {})
    assert await client.graphql("query { x }", {"a": 1}) == {"node": {"id": "PVT_1"}}
    assert wire.body(-1) == {"query": "query { x }", "variables": {"a": 1}}


async def test_pagination_follows_the_full_pages() -> None:
    full_page = [{"n": i} for i in range(100)]
    wire = Wire({("GET", "/repos/varga/x/issues"): [(200, full_page), (200, [{"n": 100}])]})
    client = GitHubClient(token="t", base_url=API, client=wire.client())
    assert (
        len(await client.paginate("/repos/varga/x/issues", repo="varga/x", params={"state": "open"})) == 101
    )
    assert [r.url.params["page"] for r in wire.requests] == ["1", "2"]
    assert wire.requests[0].url.params["per_page"] == "100"


async def test_a_pinned_installation_is_used_without_discovery(private_key: tuple[str, str]) -> None:
    pem, _ = private_key
    wire = Wire({("POST", "/app/installations/77/access_tokens"): (201, {"token": "ghs_pinned"})})
    auth = GitHubAppAuth(
        app_id="1", private_key=pem, base_url=API, pinned_installation=77, pinned_account="Varga"
    )
    async with wire.client() as client:
        token = await auth.token_for(client, "varga/billing", permissions=RUNNER_PERMISSIONS)
    assert token.token == "ghs_pinned"
    assert wire.sent() == [("POST", "/app/installations/77/access_tokens")], (
        "no GET /repos/.../installation: the claimed installation is not rediscovered"
    )


async def test_a_pinned_installation_refuses_another_owner(private_key: tuple[str, str]) -> None:
    from choregos_adapters.errors import ConfigurationError

    pem, _ = private_key
    wire = Wire({("POST", "/app/installations/77/access_tokens"): (201, {"token": "ghs_stolen"})})
    auth = GitHubAppAuth(
        app_id="1", private_key=pem, base_url=API, pinned_installation=77, pinned_account="varga"
    )
    async with wire.client() as client:
        with pytest.raises(ConfigurationError, match="belongs to `varga`"):
            await auth.token_for(client, "othercorp/private-repo")
    assert wire.requests == [], "nothing left for GitHub: no token for a repository of another owner"


async def test_list_installations_paginates(private_key: tuple[str, str]) -> None:
    pem, _ = private_key
    first = [{"id": n} for n in range(100)]
    second = [{"id": 100}]
    pages = [first, second]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/app/installations"
        return httpx.Response(200, json=pages[int(request.url.params["page"]) - 1])

    auth = GitHubAppAuth(app_id="1", private_key=pem, base_url=API)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        found = await auth.list_installations(client)
    assert [i["id"] for i in found] == list(range(101))


def test_the_connector_factory_pins_the_injected_installation(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_adapters import _github_client

    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "1")
    monkeypatch.setenv("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "pem")
    pinned = _github_client({"repo": "varga/billing", "installation_id": 5})
    assert (pinned.auth.pinned_installation, pinned.auth.pinned_account) == (5, "varga")
    discovered = _github_client({"repo": "varga/billing"})
    assert discovered.auth.pinned_installation is None, "no claim injected: discovery, as before"
