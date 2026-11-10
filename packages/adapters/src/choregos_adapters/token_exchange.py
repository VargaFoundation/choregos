# SPDX-License-Identifier: Apache-2.0
"""An RFC 8693 token-exchange client, and the refresh grant that draws a person's access token (ADR 0135).

The platform asks the deployment's identity provider to swap the PERSON'S access token (the subject
token) for one whose audience is a source's: the source then decides what that person may read. The
exchanged token is returned to the caller for one call; nothing here stores, logs or prints a token
(`ExchangedToken.access_token` is excluded from `repr`, and errors carry the provider's `error` code,
never a body that could echo a token).

`act` (RFC 8693 section 4.1): the request names the actor in `requested_actor` (`assistant:<slug>`,
and `/<installation>` when there is one). Keycloak's standard token exchange (v2) has no actor token
and ignores a parameter it does not know (RFC 6749 section 3.2), so `act` appears in the issued token
only when a protocol mapper of the realm maps it; a source that logs `act` logs it when present.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any, Final

import httpx

from .errors import AdapterError

GRANT_EXCHANGE: Final = "urn:ietf:params:oauth:grant-type:token-exchange"
TOKEN_TYPE_ACCESS: Final = "urn:ietf:params:oauth:token-type:access_token"  # noqa: S105 - a URN


class ExchangeRefused(AdapterError):  # noqa: N818 - the provider's refusal, rendered as is
    """The provider refused the exchange or the refresh (`code` is its error code, never a token)."""

    def __init__(self, code: str, status: int) -> None:
        super().__init__(f"the identity provider refused ({status}): {code}")
        self.code = code
        self.status = status


@dataclass(frozen=True)
class ExchangedToken:
    access_token: str = field(repr=False)
    expires_in: int = 0
    #: A rotated refresh token, for the refresh grant; `None` otherwise.
    refresh_token: str | None = field(default=None, repr=False)


def claims_of(token: str) -> dict[str, Any]:
    """The payload of a JWT, NOT verified: for tests and logs of claims that are not secrets."""
    try:
        payload = token.split(".")[1]
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (IndexError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


class TokenExchangeClient:
    def __init__(
        self,
        token_endpoint: str,
        *,
        client_id: str,
        client_secret: str = "",
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._endpoint = token_endpoint
        self._client_id = client_id
        self._client_secret = client_secret
        self._timeout = timeout_s
        self._transport = transport

    async def _post(self, data: dict[str, str]) -> dict[str, Any]:
        form = {"client_id": self._client_id, **data}
        if self._client_secret:
            form["client_secret"] = self._client_secret
        async with httpx.AsyncClient(timeout=self._timeout, transport=self._transport) as http:
            response = await http.post(self._endpoint, data=form)
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if response.status_code >= 400 or not body.get("access_token"):
            raise ExchangeRefused(str(body.get("error") or "invalid_response")[:80], response.status_code)
        return body

    async def exchange(
        self, subject_token: str, *, audience: str, actor: str | None = None
    ) -> ExchangedToken:
        """A token of `audience` for the person `subject_token` designates."""
        data = {
            "grant_type": GRANT_EXCHANGE,
            "subject_token": subject_token,
            "subject_token_type": TOKEN_TYPE_ACCESS,
            "requested_token_type": TOKEN_TYPE_ACCESS,
            "audience": audience,
        }
        if actor:
            data["requested_actor"] = actor
        body = await self._post(data)
        return ExchangedToken(str(body["access_token"]), int(body.get("expires_in") or 0))

    async def refresh(self, refresh_token: str) -> ExchangedToken:
        """A fresh access token of the person, from the refresh token sealed at their sign-in."""
        body = await self._post({"grant_type": "refresh_token", "refresh_token": refresh_token})
        rotated = body.get("refresh_token")
        return ExchangedToken(
            str(body["access_token"]), int(body.get("expires_in") or 0), str(rotated) if rotated else None
        )
