# SPDX-License-Identifier: Apache-2.0
"""A member's account on a code forge, proven through the forge's own OAuth 2.0 sign-in (ADR 0161).

Three providers, one shape: the authorization URL the person's browser is sent to, the exchange of the
code the forge hands back (authorization code with PKCE `S256`), and the read of the forge's user behind
the access token: its stable numeric id, its login, its avatar and its profile page.

- `github`: github.com (or a GitHub Enterprise Server by its base URL). An OAuth App asks for `read:user`;
  the client of a GitHub App is accepted too (its user authorization ignores scopes, and a GitHub App
  without any repository permission reads `/user` only).
- `gitlab`: gitlab.com or a self-managed instance, scope `read_user`.
- `forgejo`: a self-hosted Forgejo (Gitea-compatible OAuth2 provider), scope `read:user`.

The access token is used for one read and dropped: nothing here stores it, and no method returns it
past `exchange_code`. No business logic: the API decides what a proven account is allowed to do.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Literal
from urllib.parse import urlencode, urlparse

import httpx
from choregos_core.egress import EgressRefusedError

from .errors import AdapterError, ConfigurationError
from .http.egress import guarded

Provider = Literal["github", "gitlab", "forgejo"]
PROVIDERS: Final[tuple[Provider, ...]] = ("github", "gitlab", "forgejo")
#: The connector kind whose instances hold a forge's OAuth application (ADR 0161).
KIND: Final = "forge_account"
GITHUB_COM: Final = "https://github.com"
GITLAB_COM: Final = "https://gitlab.com"
#: The default base URL of each provider; Forgejo is self-hosted only, so it has none.
DEFAULT_BASE_URL: Final[dict[str, str]] = {"github": GITHUB_COM, "gitlab": GITLAB_COM}
#: The one scope each provider is asked for: enough to read who the person is, nothing more.
SCOPES: Final[dict[str, str]] = {"github": "read:user", "gitlab": "read_user", "forgejo": "read:user"}
TIMEOUT_S: Final = 10.0


def connector_type(provider: str) -> str:
    """The connector type that holds `provider`'s OAuth application: `github-oauth`..."""
    return f"{provider}-oauth"


def provider_of_type(type_name: str) -> Provider | None:
    for provider in PROVIDERS:
        if connector_type(provider) == type_name:
            return provider
    return None


def normalise_base_url(url: str) -> str:
    """`https://host[:port][/path]` without a trailing slash, query or fragment; raises otherwise.

    It is part of an account's identity (one numeric id per instance), so two spellings of the same
    instance must be the same string."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise ConfigurationError(f"`base_url` must be an https URL, not {url!r}")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError("`base_url` holds neither credentials, a query nor a fragment")
    host = parsed.hostname.lower()
    port = f":{parsed.port}" if parsed.port and parsed.port not in {80, 443} else ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{host}{port}{path}"


def base_url_of(provider: str, config: dict[str, Any]) -> str:
    raw = str(config.get("base_url") or DEFAULT_BASE_URL.get(provider) or "")
    if not raw:
        raise ConfigurationError(f"a `{provider}` instance needs its `base_url`")
    return normalise_base_url(raw)


def api_url_of(provider: str, base_url: str) -> str:
    """Where the forge's REST API answers, for the read of the user."""
    if provider == "github":
        return "https://api.github.com" if base_url == GITHUB_COM else f"{base_url}/api/v3"
    if provider == "gitlab":
        return f"{base_url}/api/v4"
    return f"{base_url}/api/v1"


def egress_of(provider: str, config: dict[str, Any]) -> list[tuple[str, int]]:
    """The hosts the platform calls for this instance: the token endpoint's and the API's."""
    try:
        base = base_url_of(provider, config)
    except ConfigurationError:
        return []
    hosts: list[tuple[str, int]] = []
    for url in (base, api_url_of(provider, base)):
        parsed = urlparse(url)
        found = (str(parsed.hostname), parsed.port or (443 if parsed.scheme == "https" else 80))
        if found not in hosts:
            hosts.append(found)
    return hosts


def check_config(provider: str, config: dict[str, Any]) -> None:
    """Saved only with an `https` `base_url` and a `client_id`."""
    base = base_url_of(provider, config)
    if not base.startswith("https://"):
        raise ConfigurationError("`base_url` must use https")
    if not str(config.get("client_id") or "").strip():
        raise ConfigurationError("`client_id` is the OAuth application's client id")


class ForgeAccountError(AdapterError):
    """The forge refused or could not be read. `code` is its error, never a token."""

    def __init__(self, code: str, status: int = 0) -> None:
        super().__init__(f"the forge answered {status or 'nothing'}: {code}")
        self.code = code
        self.status = status


@dataclass(frozen=True, slots=True)
class ForgeUser:
    """The forge's user behind a token: what a linked account records."""

    external_id: str
    login: str
    avatar_url: str | None
    profile_url: str | None


@dataclass(frozen=True, slots=True)
class ForgeAccounts:
    """One OAuth application on one forge instance."""

    provider: Provider
    base_url: str
    client_id: str
    client_secret: str = field(default="", repr=False)
    transport: httpx.AsyncBaseTransport | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.base_url.startswith("https://"):
            raise ConfigurationError("`base_url` must use https")

    @property
    def scope(self) -> str:
        return SCOPES[self.provider]

    @property
    def authorize_endpoint(self) -> str:
        path = "/oauth/authorize" if self.provider == "gitlab" else "/login/oauth/authorize"
        return f"{self.base_url}{path}"

    @property
    def token_endpoint(self) -> str:
        path = "/oauth/token" if self.provider == "gitlab" else "/login/oauth/access_token"
        return f"{self.base_url}{path}"

    @property
    def user_endpoint(self) -> str:
        return f"{api_url_of(self.provider, self.base_url)}/user"

    def authorize_url(self, *, redirect_uri: str, state: str, code_challenge: str) -> str:
        """Where the person's browser goes to sign in to the forge and grant the read of their user."""
        query = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.scope,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if self.provider == "github":
            # The account the person is signed in with is not silently assumed: they pick it.
            query["allow_signup"] = "false"
        return f"{self.authorize_endpoint}?{urlencode(query)}"

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=TIMEOUT_S, transport=guarded(self.transport), follow_redirects=False)

    async def exchange_code(self, *, code: str, redirect_uri: str, code_verifier: str) -> str:
        """The access token for `code`. Raises `ForgeAccountError` with the forge's error code."""
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self.client_id,
            "code_verifier": code_verifier,
        }
        if self.client_secret:
            form["client_secret"] = self.client_secret
        try:
            async with self._client() as http:
                response = await http.post(
                    self.token_endpoint, data=form, headers={"Accept": "application/json"}
                )
        except httpx.HTTPError as failure:
            raise ForgeAccountError(_unreachable(failure)) from failure
        body = _json_object(response)
        token = body.get("access_token")
        # GitHub answers 200 with an `error` field for a refused code.
        if response.status_code >= 400 or body.get("error") or not isinstance(token, str) or not token:
            raise ForgeAccountError(str(body.get("error") or "invalid_response")[:80], response.status_code)
        return token

    async def read_user(self, access_token: str) -> ForgeUser:
        """The forge's user behind `access_token`: its numeric id, never inferred from a login."""
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
        if self.provider == "github":
            headers["Accept"] = "application/vnd.github+json"
        try:
            async with self._client() as http:
                response = await http.get(self.user_endpoint, headers=headers)
        except httpx.HTTPError as failure:
            raise ForgeAccountError(_unreachable(failure)) from failure
        body = _json_object(response)
        if response.status_code >= 400:
            raise ForgeAccountError(str(body.get("message") or "user_unreadable")[:80], response.status_code)
        return self._user(body)

    def _user(self, body: dict[str, Any]) -> ForgeUser:
        raw_id = body.get("id")
        login = body.get("username") if self.provider == "gitlab" else body.get("login")
        if isinstance(raw_id, bool) or not isinstance(raw_id, int) or raw_id <= 0:
            raise ForgeAccountError("no_numeric_user_id")
        if not isinstance(login, str) or not login:
            raise ForgeAccountError("no_login")
        profile = body.get("web_url") if self.provider == "gitlab" else body.get("html_url")
        avatar = body.get("avatar_url")
        return ForgeUser(
            external_id=str(raw_id),
            login=login[:255],
            avatar_url=_https_or_none(avatar),
            profile_url=_https_or_none(profile) or f"{self.base_url}/{login}",
        )


def _unreachable(failure: httpx.HTTPError) -> str:
    """`egress_refused` when the organisation's allowlist kept the host closed, `unreachable` otherwise."""
    return "egress_refused" if isinstance(failure, EgressRefusedError) else "unreachable"


def _json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}


def _https_or_none(value: Any) -> str | None:
    """A URL the console may show as a link or an image: `http(s)` only, bounded."""
    if not isinstance(value, str) or len(value) > 1024:
        return None
    scheme = urlparse(value).scheme
    return value if scheme in {"https", "http"} else None


def forge_accounts(
    provider: str,
    config: dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ForgeAccounts:
    """The adapter for one instance, from its RESOLVED configuration (`client_secret` included)."""
    if provider not in PROVIDERS:
        raise ConfigurationError(f"unknown forge `{provider}`: one of {', '.join(PROVIDERS)}")
    client_id = str(config.get("client_id") or "").strip()
    if not client_id:
        raise ConfigurationError("`client_id` is the OAuth application's client id")
    return ForgeAccounts(
        provider=provider,
        base_url=base_url_of(provider, config),
        client_id=client_id,
        client_secret=str(config.get("client_secret") or ""),
        transport=transport,
    )


__all__ = [
    "DEFAULT_BASE_URL",
    "GITHUB_COM",
    "GITLAB_COM",
    "KIND",
    "PROVIDERS",
    "SCOPES",
    "ForgeAccountError",
    "ForgeAccounts",
    "ForgeUser",
    "Provider",
    "api_url_of",
    "base_url_of",
    "check_config",
    "connector_type",
    "egress_of",
    "forge_accounts",
    "normalise_base_url",
    "provider_of_type",
]
