"""The three Azure DevOps connectors share one client module (ADR 0198, S13-08 follow-up)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import httpx
import pytest
from tranibor_adapters import azure_devops
from tranibor_adapters.azure_devops import AzureDevOpsClient, EntraClientCredentials, azure_boards_egress
from tranibor_adapters.builtins import _azure_boards_tracker, _azure_pipelines_ci, _azure_repos_scm
from tranibor_adapters.errors import UpstreamError
from tranibor_adapters.specs import _check_azure_boards
from tranibor_adapters.tracker import azure_boards

SOURCE = Path(azure_devops.__file__).parent
BASE = "https://dev.azure.com/northwind"
CFG: dict[str, Any] = {"base_url": BASE, "project": "Billing", "repository": "api", "pat": "x"}
ENTRA: dict[str, Any] = {
    **CFG,
    "pat": None,
    "auth": "entra",
    "tenant_id": "t",
    "client_id": "c",
    "client_secret": "s",
}


@pytest.fixture(autouse=True)
def _no_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TRANIBOR_FAKES", raising=False)


def test_the_three_builders_return_adapters_on_the_same_client_class() -> None:
    for cfg in (CFG, ENTRA):
        clients = [
            _azure_boards_tracker(cfg).client,
            _azure_repos_scm(cfg).client,
            _azure_pipelines_ci(cfg).client,
        ]
        assert {type(client) for client in clients} == {AzureDevOpsClient}
        assert [client.service for client in clients] == ["azure-boards", "azure-repos", "azure-pipelines"]
        if cfg is ENTRA:
            assert {type(client.entra) for client in clients} == {EntraClientCredentials}


def test_no_second_client_token_or_secret_reader_exists() -> None:
    defined: dict[str, list[str]] = {}
    for path in SOURCE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                defined.setdefault(node.name, []).append(path.name)
    for name in ("AzureDevOpsClient", "EntraClientCredentials", "shared_secret_of", "azure_client"):
        assert defined[name] == ["azure_devops.py"], f"{name} must be defined once, found {defined[name]}"
    assert azure_boards.AzureDevOpsClient is AzureDevOpsClient
    assert azure_boards.shared_secret_of is azure_devops.shared_secret_of
    assert azure_boards.EntraClientCredentials is EntraClientCredentials
    assert _check_azure_boards is azure_devops.check_azure_boards


async def test_boards_names_a_pat_answered_by_a_sign_in_page() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(203, text="<html>sign in</html>")

    client = AzureDevOpsClient(
        BASE,
        pat="bad",
        service="azure-boards",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    tracker = azure_boards.AzureBoardsTracker(client, "Billing")
    with pytest.raises(UpstreamError, match="203"):
        await tracker.fetch_item("1")
    assert (await tracker.test())["ok"] is False


async def test_a_refused_principal_names_the_connector_that_asked() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    client = azure_devops.azure_client(ENTRA, service="azure-pipelines")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(UpstreamError) as refused:
        await client.request("GET", "/x")
    assert refused.value.service == "azure-pipelines"


async def test_a_busy_service_is_retried_and_a_redirect_is_not_followed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr("tranibor_adapters.http.rest.asyncio.sleep", no_sleep)
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/moved":
            return httpx.Response(302, headers={"Location": "https://evil.example/"})
        return (
            httpx.Response(200, json={})
            if len(calls) > 2
            else httpx.Response(503, headers={"Retry-After": "1"})
        )

    client = AzureDevOpsClient(
        BASE, pat="x", client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    assert await client.request("GET", "/ok") == {}
    assert len(calls) == 3
    await client.request("GET", "/moved")  # the 302 is answered as it is: no second request is made
    assert "/moved" in calls[-1]
    assert all("evil.example" not in url for url in calls)


def test_boards_egress_follows_the_login_url_and_skips_the_identity_host() -> None:
    assert azure_boards_egress(CFG) == [("dev.azure.com", 443)]
    cfg = {**ENTRA, "login_url": "https://login.sovereign.example"}
    assert azure_boards_egress(cfg) == [("dev.azure.com", 443), ("login.sovereign.example", 443)]
    assert ("vssps.dev.azure.com", 443) in azure_devops.azure_egress(CFG)
