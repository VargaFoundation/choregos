# SPDX-License-Identifier: Apache-2.0
"""A scriptable fake identity provider (ADR 0005): the token endpoint of ADR 0135, in memory.

It serves the RFC 8693 exchange and the refresh grant. Tokens it issues are unsigned JWT-shaped
strings (`claims_of` reads them): `sub` is the person, `aud` the requested audience, `act` the actor
when the request names one (as a provider with a mapper would). Every request is recorded."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

from ..token_exchange import GRANT_EXCHANGE

ENDPOINT = "http://fake-idp.test/token"


def _jwt(claims: dict[str, Any]) -> str:
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part(claims)}.fake"


@dataclass
class FakeIdentityProvider:
    #: access token of a person -> their subject
    access_tokens: dict[str, str] = field(default_factory=dict)
    #: refresh token -> subject
    refresh_tokens: dict[str, str] = field(default_factory=dict)
    #: the audiences a client may exchange towards; `None`: any
    allowed_audiences: set[str] | None = None
    #: a refresh grant answers a NEW refresh token (rotation)
    rotate: bool = False
    #: forms of every request received
    requests: list[dict[str, str]] = field(default_factory=list)
    issued: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.respond)

    def respond(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.requests.append(form)
        grant = form.get("grant_type", "")
        if grant == GRANT_EXCHANGE:
            subject = self.access_tokens.get(form.get("subject_token", ""))
            if subject is None:
                return httpx.Response(400, json={"error": "invalid_token"})
            audience = form.get("audience", "")
            if self.allowed_audiences is not None and audience not in self.allowed_audiences:
                return httpx.Response(403, json={"error": "access_denied"})
            claims: dict[str, Any] = {"sub": subject, "aud": audience, "azp": form.get("client_id", "")}
            if form.get("requested_actor"):
                claims["act"] = {"sub": form["requested_actor"]}
            self.issued += 1
            return httpx.Response(
                200, json={"access_token": _jwt({**claims, "jti": self.issued}), "expires_in": 60}
            )
        if grant == "refresh_token":
            subject = self.refresh_tokens.get(form.get("refresh_token", ""))
            if subject is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.issued += 1
            fresh = f"access-of-{subject}-{self.issued}"
            self.access_tokens[fresh] = subject
            body: dict[str, Any] = {"access_token": fresh, "expires_in": 60}
            if self.rotate:
                body["refresh_token"] = f"refresh-of-{subject}-{self.issued}"
                self.refresh_tokens[body["refresh_token"]] = subject
            return httpx.Response(200, json=body)
        return httpx.Response(400, json={"error": "unsupported_grant_type"})


def current_fake_identity_provider() -> FakeIdentityProvider:
    """The fake identity provider of CHOREGOS_FAKES=1: one, scripted by the tests and the demo."""
    import choregos_adapters as package

    if package.FAKE_IDP is None:
        package.FAKE_IDP = FakeIdentityProvider()
    found: FakeIdentityProvider = package.FAKE_IDP
    return found
