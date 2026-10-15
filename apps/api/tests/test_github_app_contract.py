"""S40-04a: the GitHub App contract is declared and served (501 until S40-04b)."""

from __future__ import annotations

from typing import Any

import choregos_contracts as contracts

OPERATIONS = {
    "getGitHubAppStatus": ("get", "/orgs/{org}/github"),
    "refreshGitHubInstallations": ("post", "/orgs/{org}/github/refresh"),
    "listGitHubRepositories": ("get", "/orgs/{org}/github/repositories"),
    "createGitHubInstallLink": ("post", "/orgs/{org}/github/install-link"),
    "githubAppSetup": ("get", "/github/setup"),
}


def test_the_contract_declares_the_five_operations() -> None:
    paths = contracts.load_openapi()["paths"]
    for operation_id, (verb, path) in OPERATIONS.items():
        assert paths[path][verb]["operationId"] == operation_id


def test_the_setup_callback_is_a_redirect_not_json() -> None:
    operation = contracts.load_openapi()["paths"]["/github/setup"]["get"]
    assert "302" in operation["responses"]
    assert "200" not in operation["responses"]


def test_no_installation_id_is_ever_a_request_field() -> None:
    schemas = contracts.load_openapi()["components"]["schemas"]
    assert "installation_id" not in schemas["GitHubInstallLinkRequest"]["properties"]


async def test_the_stubs_answer_501(client: Any) -> None:
    response = await client.get("/api/v1/orgs/varga/github")
    assert response.status_code == 501
