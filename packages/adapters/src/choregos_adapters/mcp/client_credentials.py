# SPDX-License-Identifier: Apache-2.0
"""OAuth 2.0 client credentials for an MCP server (RFC 6749 §4.4): the token the platform fetches,
caches and renews for a connector. The agent never holds it: the broker does.

Two callers share it: a direct `mcp` connector whose `auth_scheme` is `oauth_client_credentials`
(`builtins._mcp_auth`), and the organisation's MCP gateway signed in the same way (ADR 0105,
`choregos_api.services.mcp_gateway`). What they share:

- the request: `grant_type=client_credentials`, the client authenticated with HTTP Basic
  (`client_secret_basic`, the method every token endpoint must accept, RFC 6749 §2.3.1), and the
  `scope`, `audience` and `resource` (RFC 8707) the caller asks for, each only when set;
- the transport: guarded (ADR 0066), so the token endpoint's host is checked against the
  organisation's egress list on every request, like the server's own;
- the cache: a token is reused until `TOKEN_SKEW_S` before the `expires_in` it came with
  (`DEFAULT_TOKEN_TTL_S` when the endpoint says nothing), and fetched again when the server answers
  401 (`refresh=True`, which the MCP client asks for once per request);
- the secrecy: the token and the client secret are remembered for redaction, never logged, never
  part of an error message, and leave the process only in the header of the request they sign.

A token endpoint that gives no token is a `TokenRefused`: an `McpError` that every caller already
reports (a tool error, a failed discovery, a failed test), naming the endpoint's host, the client id
and the OAuth error code, never the secret or the response's body.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Final
from urllib.parse import urlsplit

import httpx
from choregos_core.redaction import remember_secret

from ..http.egress import guarded
from .client import HeadersProvider, McpError

#: A cached token is renewed this long before it expires.
TOKEN_SKEW_S: Final = 30
#: How long a token is taken to live when the endpoint gives no `expires_in`.
DEFAULT_TOKEN_TTL_S: Final = 300
#: Beyond this many entries, the expired ones are dropped before a new one is kept.
MAX_CACHED: Final = 1024
#: RFC 6749 §5.2 error codes are short ASCII words: anything else is not echoed.
_OAUTH_ERROR = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


def _now() -> float:
    """The cache's clock (monotonic): tests move it."""
    return time.monotonic()


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@dataclass(frozen=True)
class ClientCredentials:
    """One OAuth client, and what it asks its token endpoint for."""

    token_url: str
    client_id: str
    client_secret: str = field(repr=False)
    scope: str | None = None
    audience: str | None = None
    #: RFC 8707's resource indicator: the gateway sends its endpoint's origin; a direct connector, none.
    resource: str | None = None

    def form(self) -> dict[str, str]:
        form = {"grant_type": "client_credentials"}
        for name in ("scope", "audience", "resource"):
            value = getattr(self, name)
            if value:
                form[name] = value
        return form

    def key(self, partition: str) -> str:
        """The cache key: the client, what it asks for, and a digest of its secret, so that a new
        secret never reuses a token the former one obtained."""
        parts = [
            partition,
            self.token_url,
            self.client_id,
            self.scope or "",
            self.audience or "",
            self.resource or "",
            _digest(self.client_secret),
        ]
        return _digest("|".join(parts))

    @property
    def host(self) -> str:
        return urlsplit(self.token_url).hostname or self.token_url


class TokenRefused(McpError):  # noqa: N818 - a motivated refusal, reported where the call was made
    """The token endpoint gave no token. `code` is its HTTP status; `oauth_error` its RFC 6749 error code
    (`invalid_client` for a wrong secret), when it gave a readable one."""

    kind: ClassVar[str] = "oauth_token_refused"

    def __init__(self, message: str, status: int, oauth_error: str | None) -> None:
        super().__init__(message, status)
        self.oauth_error = oauth_error


def _oauth_error(body: Any) -> str | None:
    error = body.get("error") if isinstance(body, dict) else None
    return error if isinstance(error, str) and _OAUTH_ERROR.fullmatch(error) else None


def _lifetime(body: Mapping[str, Any]) -> float:
    raw = body.get("expires_in")
    if isinstance(raw, str) and raw.strip().isdigit():
        raw = int(raw.strip())
    if isinstance(raw, int | float) and not isinstance(raw, bool) and raw > 0:
        return float(raw)
    return float(DEFAULT_TOKEN_TTL_S)


class TokenCache:
    """Tokens by client, until shortly before they expire. `label` names the endpoint in a refusal;
    without one, the refusal names the token endpoint's host."""

    def __init__(self, label: str | None = None) -> None:
        self._label = label
        self._tokens: dict[str, tuple[str, float]] = {}

    def clear(self) -> None:
        self._tokens.clear()

    def __len__(self) -> int:
        return len(self._tokens)

    def _keep(self, key: str, token: str, until: float) -> None:
        if len(self._tokens) >= MAX_CACHED:
            now = _now()
            self._tokens = {k: v for k, v in self._tokens.items() if v[1] > now}
        self._tokens[key] = (token, until)

    def _refused(self, credentials: ClientCredentials, response: httpx.Response, body: Any) -> TokenRefused:
        label = self._label or f"the token endpoint `{credentials.host}`"
        error = _oauth_error(body)
        detail = f"HTTP {response.status_code}" + (f", {error}" if error else "")
        if response.status_code in {400, 401} or error:
            message = (
                f"{label} refused the OAuth client `{credentials.client_id}` ({detail}): check its "
                "client id, its client secret and the scope it asks for"
            )
        else:
            message = f"{label} gave no access token ({detail})"
        return TokenRefused(message, response.status_code, error)

    async def token(
        self,
        credentials: ClientCredentials,
        *,
        refresh: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout_s: float = 30.0,
        partition: str = "",
    ) -> str:
        """The client's token: the cached one unless `refresh` or it is about to expire. The request goes
        through the guarded transport (ADR 0066)."""
        remember_secret(credentials.client_secret)
        key = credentials.key(partition)
        cached = self._tokens.get(key)
        if cached is not None and not refresh and cached[1] > _now():
            return cached[0]
        async with httpx.AsyncClient(transport=guarded(transport), timeout=timeout_s) as http:
            response = await http.post(
                credentials.token_url,
                data=credentials.form(),
                auth=httpx.BasicAuth(credentials.client_id, credentials.client_secret),
                headers={"Accept": "application/json"},
            )
        body: Any = None
        try:
            body = response.json()
        except ValueError:
            body = None
        token = body.get("access_token") if isinstance(body, dict) and response.status_code == 200 else None
        if not isinstance(token, str) or not token:
            raise self._refused(credentials, response, body)
        remember_secret(token)
        assert isinstance(body, dict)
        self._keep(key, token, _now() + _lifetime(body) - TOKEN_SKEW_S)
        return token

    def provider(
        self,
        credentials: ClientCredentials,
        *,
        transport: Callable[[], httpx.AsyncBaseTransport | None] | None = None,
        timeout_s: float = 30.0,
        partition: str = "",
    ) -> HeadersProvider:
        """The MCP client's headers provider: `Authorization: Bearer <token>`, renewed on `refresh`.
        `transport` is read per request (the tests' fake; `None`: the real network)."""

        async def headers(refresh: bool) -> dict[str, str]:
            token = await self.token(
                credentials,
                refresh=refresh,
                transport=transport() if transport is not None else None,
                timeout_s=timeout_s,
                partition=partition,
            )
            return {"Authorization": f"Bearer {token}"}

        return headers


#: The tokens of the direct `mcp` connectors of this process.
CONNECTOR_TOKENS: Final = TokenCache()


__all__ = [
    "CONNECTOR_TOKENS",
    "DEFAULT_TOKEN_TTL_S",
    "TOKEN_SKEW_S",
    "ClientCredentials",
    "TokenCache",
    "TokenRefused",
]
