"""The GitHub App Manifest flow's conversion, and where the adapters read the App from (S70-21, ADR 0161)."""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import respx
from choregos_adapters.errors import UpstreamError
from choregos_adapters.fakes.github_app import FakeManifestConversion
from choregos_adapters.github import GitHubManifestConversion, app_credentials, set_app_credentials_provider

CONVERSION = "https://api.github.com/app-manifests/abc123/conversions"


@pytest.fixture(autouse=True)
def no_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from choregos_adapters.github import manifest

    kept = list(manifest._PROVIDER)
    set_app_credentials_provider(None)
    monkeypatch.delenv("CHOREGOS_GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("CHOREGOS_GITHUB_APP_PRIVATE_KEY", raising=False)
    yield
    manifest._PROVIDER[:] = kept


async def test_the_code_is_exchanged_without_authentication() -> None:
    answer = {"id": 7, "slug": "x", "pem": "k", "webhook_secret": "w", "client_id": "c", "client_secret": "s"}
    with respx.mock(assert_all_called=True) as mock:
        route = mock.post(CONVERSION).mock(return_value=httpx.Response(201, json=answer))
        assert await GitHubManifestConversion().convert("abc123") == answer
    assert "authorization" not in route.calls.last.request.headers


@pytest.mark.parametrize("status", [404, 422])
async def test_a_refused_code_is_an_upstream_error(status: int) -> None:
    with respx.mock() as mock:
        mock.post(CONVERSION).mock(return_value=httpx.Response(status, json={"message": "Not Found"}))
        with pytest.raises(UpstreamError, match=str(status)):
            await GitHubManifestConversion().convert("abc123")


@pytest.mark.parametrize("code", ["", "../app/installations", "a?b", "x" * 201])
async def test_a_code_that_is_not_githubs_never_reaches_the_network(code: str) -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route()
        with pytest.raises(UpstreamError):
            await GitHubManifestConversion().convert(code)
    assert not route.called


async def test_the_fake_answers_a_scripted_code_once() -> None:
    fake = FakeManifestConversion()
    fake.register("c1", id=9)
    assert (await fake.convert("c1"))["id"] == 9
    with pytest.raises(UpstreamError, match="404"):
        await fake.convert("c1")
    fake.refuse("c2", 422)
    with pytest.raises(UpstreamError, match="422"):
        await fake.convert("c2")
    assert fake.calls == ["c1", "c1", "c2"]


def test_the_deployments_variables_win_over_a_stored_app(monkeypatch: pytest.MonkeyPatch) -> None:
    assert app_credentials() == ("", "")
    set_app_credentials_provider(lambda: ("42", "stored-key"))
    assert app_credentials() == ("42", "stored-key")
    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "1")
    monkeypatch.setenv("CHOREGOS_GITHUB_APP_PRIVATE_KEY", "deployment-key")
    assert app_credentials() == ("1", "deployment-key")


def test_the_github_client_reads_the_stored_app_when_the_deployment_names_none() -> None:
    from choregos_adapters.builtins import _github_client

    assert _github_client({"repo": "acme/app"}).auth is None
    set_app_credentials_provider(lambda: ("42", "stored-key"))
    auth = _github_client({"repo": "acme/app"}).auth
    assert (auth.app_id, auth.private_key) == ("42", "stored-key")
