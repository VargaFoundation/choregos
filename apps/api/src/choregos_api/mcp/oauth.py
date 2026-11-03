# SPDX-License-Identifier: Apache-2.0
"""The MCP door as an OAuth resource server (RFC 9728): the identity provider issues the tokens (ADR 0030).

claude.ai, Claude Desktop and the other hosted clients connect through OAuth: they read the
resource metadata, find the identity provider, get a token there for the user, and present it. The
platform issues no secret for them (R-SOC-MCP-02); it VERIFIES:

- the signature, with the JWKS the issuer publishes (cached; an unknown `kid` reloads it once, to
  follow a key rotation);
- the issuer, the expiry, and the **audience** of the door, which is mandatory: on a shared realm,
  a token obtained by another application must not open it;
- asymmetric algorithms only: `alg: none` and HS256 are refused.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import jwt

from ..config import Settings

ALGORITHMS = ("RS256", "RS384", "RS512", "ES256", "ES384", "PS256")
CACHE_TTL_S = 3600.0


class TokenRefused(Exception):  # noqa: N818 - a reasoned refusal, rendered as a 401
    pass


def issuer_of(settings: Settings) -> str:
    return (settings.mcp_oauth_issuer or settings.oidc_issuer).rstrip("/")


def metadata(settings: Settings, path: str = "/mcp") -> dict[str, Any]:
    """The RFC 9728 document. `resource` is EXACTLY the URL the user types."""
    return {
        "resource": f"{settings.public_url.rstrip('/')}{path}",
        "authorization_servers": [issuer_of(settings)],
        "scopes_supported": ["mcp:read", "mcp:write"],
        "bearer_methods_supported": ["header"],
        "resource_name": "Choregos",
    }


class _Keys:
    """The issuer's public keys, cached."""

    def __init__(self) -> None:
        self._by_issuer: dict[str, tuple[float, dict[str, jwt.PyJWK]]] = {}

    async def _load(self, issuer: str) -> dict[str, jwt.PyJWK]:
        async with httpx.AsyncClient(timeout=10) as http:
            response = await http.get(f"{issuer}/.well-known/openid-configuration")
            discovery = response.raise_for_status().json()
            key_set = (await http.get(discovery["jwks_uri"])).raise_for_status().json()
        keys = {}
        for raw in key_set.get("keys", []):
            if raw.get("use", "sig") == "sig" and raw.get("kid"):
                try:
                    keys[str(raw["kid"])] = jwt.PyJWK(raw)
                except jwt.PyJWKError:
                    continue
        self._by_issuer[issuer] = (time.monotonic(), keys)
        return keys

    async def key(self, issuer: str, kid: str) -> jwt.PyJWK:
        stamp, keys = self._by_issuer.get(issuer, (0.0, {}))
        if kid not in keys or time.monotonic() - stamp > CACHE_TTL_S:
            keys = await self._load(issuer)
        if kid not in keys:
            raise TokenRefused(f"key `{kid}` unknown to the issuer")
        return keys[kid]

    def clear(self) -> None:
        self._by_issuer.clear()


KEYS = _Keys()


async def validate(raw: str, settings: Settings) -> dict[str, Any]:
    """The claims of a valid token, or `TokenRefused`."""
    try:
        header = jwt.get_unverified_header(raw)
    except jwt.PyJWTError as error:
        raise TokenRefused("unreadable token") from error
    if header.get("alg") not in ALGORITHMS:
        raise TokenRefused(f"algorithm refused: {header.get('alg')}")
    issuer = issuer_of(settings)
    try:
        key = await KEYS.key(issuer, str(header.get("kid") or ""))
        claims: dict[str, Any] = jwt.decode(
            raw,
            key=key.key,
            algorithms=[str(header["alg"])],
            audience=settings.mcp_oauth_audience,
            issuer=issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except (jwt.PyJWTError, httpx.HTTPError) as error:
        raise TokenRefused(f"token refused: {error}") from error
    return claims


emetteur = issuer_of  # lang: fr-ok alias:internal
