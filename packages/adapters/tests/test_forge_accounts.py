# SPDX-License-Identifier: Apache-2.0
"""A member's forge account, proven through the forge's OAuth sign-in (ADR 0161): GitHub, GitLab, Forgejo.

The authorization URL asks for the one scope that reads the user and carries PKCE `S256`; the code is
exchanged with the verifier and the client secret; the user read is the forge's numeric id, in each
provider's own shape. Against the fake forge, which checks what a real one checks."""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from choregos_adapters import build, egress_hosts, validate_config
from choregos_adapters.errors import AdapterError, ConfigurationError
from choregos_adapters.fakes.forge_accounts import FakeForge
from choregos_adapters.forge_accounts import (
    ForgeAccountError,
    ForgeAccounts,
    forge_accounts,
    normalise_base_url,
)

VERIFIER = "v" * 64
REDIRECT = "https://api.example.test/api/v1/auth/linked-accounts/callback"


def _challenge(verifier: str = VERIFIER) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _adapter(fake: FakeForge, provider: str, base_url: str | None = None) -> ForgeAccounts:
    config = {"client_id": fake.client_id, "client_secret": fake.client_secret}
    if base_url:
        config["base_url"] = base_url
    return forge_accounts(provider, config, transport=fake.transport())


@pytest.mark.parametrize(
    ("provider", "base_url", "authorize", "token", "user", "scope"),
    [
        (
            "github",
            None,
            "https://github.com/login/oauth/authorize",
            "https://github.com/login/oauth/access_token",
            "https://api.github.com/user",
            "read:user",
        ),
        (
            "github",
            "https://ghe.corp.example",
            "https://ghe.corp.example/login/oauth/authorize",
            "https://ghe.corp.example/login/oauth/access_token",
            "https://ghe.corp.example/api/v3/user",
            "read:user",
        ),
        (
            "gitlab",
            None,
            "https://gitlab.com/oauth/authorize",
            "https://gitlab.com/oauth/token",
            "https://gitlab.com/api/v4/user",
            "read_user",
        ),
        (
            "forgejo",
            "https://forge.corp.example/",
            "https://forge.corp.example/login/oauth/authorize",
            "https://forge.corp.example/login/oauth/access_token",
            "https://forge.corp.example/api/v1/user",
            "read:user",
        ),
    ],
)
async def test_each_forge_proves_the_signed_in_user_by_its_numeric_id(
    provider: str, base_url: str | None, authorize: str, token: str, user: str, scope: str
) -> None:
    fake = FakeForge()
    fake.user("octo", 4242)
    adapter = _adapter(fake, provider, base_url)
    url = adapter.authorize_url(redirect_uri=REDIRECT, state="the-state", code_challenge=_challenge())
    parsed = urlparse(url)
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == authorize
    assert query["scope"] == scope and query["code_challenge_method"] == "S256"
    assert query["state"] == "the-state" and query["redirect_uri"] == REDIRECT

    back = parse_qs(urlparse(fake.authorize(url, "octo")).query)
    access = await adapter.exchange_code(code=back["code"][0], redirect_uri=REDIRECT, code_verifier=VERIFIER)
    found = await adapter.read_user(access)

    assert found.external_id == "4242" and found.login == "octo"
    assert found.avatar_url and found.profile_url and found.profile_url.endswith("/octo")
    assert [str(r.url) for r in fake.requests] == [token, user]
    exchange = parse_qs(fake.requests[0].content.decode())
    assert exchange["code_verifier"] == [VERIFIER] and exchange["client_secret"] == [fake.client_secret]


async def test_a_wrong_verifier_or_a_replayed_code_is_refused() -> None:
    fake = FakeForge()
    adapter = _adapter(fake, "github")
    url = adapter.authorize_url(redirect_uri=REDIRECT, state="s", code_challenge=_challenge())
    code = parse_qs(urlparse(fake.authorize(url)).query)["code"][0]
    with pytest.raises(ForgeAccountError) as wrong:
        await adapter.exchange_code(code=code, redirect_uri=REDIRECT, code_verifier="w" * 64)
    assert wrong.value.code == "invalid_grant"
    with pytest.raises(ForgeAccountError) as replayed:
        await adapter.exchange_code(code=code, redirect_uri=REDIRECT, code_verifier=VERIFIER)
    assert replayed.value.code == "bad_verification_code", "GitHub answers 200 with an error field"


async def test_a_user_without_a_numeric_id_is_not_an_account() -> None:
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "octo", "login": "octo"})

    adapter = forge_accounts("gitlab", {"client_id": "c"}, transport=httpx.MockTransport(respond))
    with pytest.raises(ForgeAccountError) as refused:
        await adapter.read_user("t")
    assert refused.value.code == "no_numeric_user_id"


async def test_an_unreadable_user_is_an_error_naming_the_status() -> None:
    adapter = forge_accounts(
        "forgejo",
        {"client_id": "c", "base_url": "https://forge.example"},
        transport=httpx.MockTransport(lambda r: httpx.Response(401, json={"message": "Bad credentials"})),
    )
    with pytest.raises(ForgeAccountError) as refused:
        await adapter.read_user("t")
    assert refused.value.status == 401


def test_the_base_url_is_one_spelling_per_instance() -> None:
    assert normalise_base_url("https://Forge.Example:443/git/") == "https://forge.example/git"
    assert normalise_base_url("https://forge.example:8443") == "https://forge.example:8443"
    for bad in (
        "ftp://forge.example",
        "https://user:pw@forge.example",
        "https://forge.example/?a=1",
        "forge",
    ):
        with pytest.raises(ConfigurationError):
            normalise_base_url(bad)


def test_the_connector_types_declare_their_egress_and_check_their_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)  # the real factory, whatever ran before
    assert egress_hosts("forge_account", "github-oauth", {"client_id": "c"}) == [
        ("github.com", 443),
        ("api.github.com", 443),
    ]
    assert egress_hosts("forge_account", "forgejo-oauth", {"base_url": "https://git.corp.example"}) == [
        ("git.corp.example", 443)
    ]
    validate_config("forge_account", "gitlab-oauth", {"client_id": "c"})
    with pytest.raises(AdapterError):
        validate_config("forge_account", "forgejo-oauth", {"client_id": "c"})
    with pytest.raises(AdapterError):
        validate_config("forge_account", "github-oauth", {"client_id": "c", "base_url": "http://github.com"})
    built = build("forge_account", "gitlab-oauth", {"client_id": "c", "client_secret": "s3cret"})
    assert isinstance(built, ForgeAccounts) and built.provider == "gitlab"
    assert "s3cret" not in repr(built), "the client secret never reaches a log line"
