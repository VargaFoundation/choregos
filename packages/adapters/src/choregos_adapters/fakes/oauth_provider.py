# SPDX-License-Identifier: Apache-2.0
"""A scriptable fake OAuth provider (ADR 0005): the authorization server of a `user_consent` source, and
the token endpoint of an `mcp` connector signed in with `oauth_client_credentials`.

It serves what ADR 0135 point 1 needs of a source's own authorization server, in memory:

- `authorize(url, subject)` plays the person consenting in their browser: it checks the authorization
  request (client, redirect URI, `response_type=code`, PKCE `S256`) and answers the redirect the browser
  would follow, `redirect_uri?code=...&state=...`;
- `POST /token` takes the `authorization_code` grant (the PKCE verifier must match the challenge, the
  client secret must match when the provider has one) and the `refresh_token` grant (a new refresh token
  when `rotate`, the old one then dead);
- `POST /revoke` (RFC 7009) kills a refresh token when `revocation` is on.

And what a machine client needs (RFC 6749 §4.4): `POST /token` takes the `client_credentials` grant, the
client authenticated with HTTP Basic or in the form, and answers a token of `client_token_ttl_s`. A server
that trusts this provider asks `accepts(token)`; `expire_client_tokens()` plays the provider's clock
running out, so the next call with a cached token is refused.

Tokens are opaque strings that name the person (or the client), so a test reads WHOSE token a source
received. Every request is recorded as it was sent: a client that authenticates with HTTP Basic leaves no
secret in its form."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

BASE = "http://fake-oauth.test"
AUTHORIZE = f"{BASE}/authorize"
TOKEN = f"{BASE}/token"
REVOKE = f"{BASE}/revoke"


def _jwt(claims: dict[str, Any]) -> str:
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'none'})}.{part(claims)}.fake"


@dataclass
class FakeOAuthProvider:
    client_id: str = "choregos-client"
    #: `""`: a public client; otherwise the secret the token and revocation endpoints require.
    client_secret: str = ""
    #: a refresh grant answers a NEW refresh token and kills the old one
    rotate: bool = False
    #: whether `/revoke` exists (a provider may offer none)
    revocation: bool = True
    #: code -> (challenge, redirect URI, subject, scope)
    codes: dict[str, tuple[str, str, str, str]] = field(default_factory=dict)
    #: refresh token -> (subject, scope)
    refresh_tokens: dict[str, tuple[str, str]] = field(default_factory=dict)
    #: access token -> subject
    access_tokens: dict[str, str] = field(default_factory=dict)
    revoked: list[str] = field(default_factory=list)
    #: `(path, form)` of every request received at the token and revocation endpoints, as sent
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    issued: int = 0
    #: the `expires_in` of a `client_credentials` token
    client_token_ttl_s: int = 300
    #: `client_credentials` token -> its expiry on the monotonic clock
    client_tokens: dict[str, float] = field(default_factory=dict)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.respond)

    def authorize(self, url: str, subject: str = "provider-user") -> str:
        """The person consents: the redirect the browser follows, or a `ValueError` for a bad request."""
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if query.get("client_id") != self.client_id:
            raise ValueError("unknown client_id")
        if query.get("response_type") != "code":
            raise ValueError("response_type must be code")
        if query.get("code_challenge_method") != "S256" or not query.get("code_challenge"):
            raise ValueError("PKCE S256 is required")
        redirect = query.get("redirect_uri", "")
        if not redirect:
            raise ValueError("redirect_uri is required")
        self.issued += 1
        code = f"code-{self.issued}"
        self.codes[code] = (query["code_challenge"], redirect, subject, query.get("scope", ""))
        return f"{redirect}?{urlencode({'code': code, 'state': query.get('state', '')})}"

    def _client_ok(self, form: dict[str, str]) -> bool:
        return not self.client_secret or form.get("client_secret") == self.client_secret

    def _basic_client(self, request: httpx.Request, form: dict[str, str]) -> dict[str, str]:
        """The client of HTTP Basic authentication (RFC 6749 §2.3.1) moved into the form, where the
        authorization-code requests carry it."""
        scheme, _, value = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "basic":
            return form
        try:
            client_id, _, secret = base64.b64decode(value).decode().partition(":")
        except ValueError:
            return {**form, "client_id": ""}
        return {**form, "client_id": client_id, "client_secret": secret}

    def respond(self, request: httpx.Request) -> httpx.Response:
        sent = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        path = request.url.path
        self.requests.append((path, sent))
        form = self._basic_client(request, sent)
        if form.get("client_id") != self.client_id or not self._client_ok(form):
            return httpx.Response(401, json={"error": "invalid_client"})
        if path == "/revoke":
            return self._revoke(form)
        grant = form.get("grant_type", "")
        if grant == "authorization_code":
            return self._code_grant(form)
        if grant == "refresh_token":
            return self._refresh_grant(form)
        if grant == "client_credentials":
            return self._client_grant(form)
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def _client_grant(self, form: dict[str, str]) -> httpx.Response:
        if not self.client_secret:  # a public client acts for nobody of its own
            return httpx.Response(401, json={"error": "invalid_client"})
        self.issued += 1
        access = f"access-of-client-{self.client_id}-{self.issued}"
        self.client_tokens[access] = time.monotonic() + self.client_token_ttl_s
        body = {"access_token": access, "token_type": "Bearer", "expires_in": self.client_token_ttl_s}
        if form.get("scope"):
            body["scope"] = form["scope"]
        return httpx.Response(200, json=body)

    def accepts(self, token: str) -> bool:
        """Whether a server that trusts this provider takes `token`: a `client_credentials` token it issued,
        not expired."""
        expiry = self.client_tokens.get(token)
        return expiry is not None and expiry > time.monotonic()

    def expire_client_tokens(self) -> None:
        """Every `client_credentials` token issued so far has expired, whatever its client still caches."""
        for token in self.client_tokens:
            self.client_tokens[token] = 0.0

    def client_grants(self) -> list[dict[str, str]]:
        """The forms of the `client_credentials` requests received, in order."""
        return [form for _, form in self.requests if form.get("grant_type") == "client_credentials"]

    def _revoke(self, form: dict[str, str]) -> httpx.Response:
        if not self.revocation:
            return httpx.Response(404)
        token = form.get("token", "")
        self.revoked.append(token)
        self.refresh_tokens.pop(token, None)
        return httpx.Response(200)

    def _mint(self, subject: str, scope: str, *, with_id_token: bool) -> dict[str, Any]:
        self.issued += 1
        access = f"access-of-{subject}-{self.issued}"
        self.access_tokens[access] = subject
        body: dict[str, Any] = {"access_token": access, "expires_in": 3600, "scope": scope}
        if with_id_token:
            body["id_token"] = _jwt({"sub": subject, "aud": self.client_id})
        return body

    def _code_grant(self, form: dict[str, str]) -> httpx.Response:
        issued = self.codes.pop(form.get("code", ""), None)
        if issued is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        challenge, redirect, subject, scope = issued
        digest = hashlib.sha256(form.get("code_verifier", "").encode("ascii")).digest()
        verifier_challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
        if verifier_challenge != challenge or form.get("redirect_uri") != redirect:
            return httpx.Response(400, json={"error": "invalid_grant"})
        body = self._mint(subject, scope, with_id_token=True)
        refresh = f"refresh-of-{subject}-{self.issued}"
        self.refresh_tokens[refresh] = (subject, scope)
        body["refresh_token"] = refresh
        return httpx.Response(200, json=body)

    def _refresh_grant(self, form: dict[str, str]) -> httpx.Response:
        token = form.get("refresh_token", "")
        found = self.refresh_tokens.get(token)
        if found is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        subject, scope = found
        body = self._mint(subject, scope, with_id_token=False)
        if self.rotate:
            del self.refresh_tokens[token]
            rotated = f"refresh-of-{subject}-{self.issued}"
            self.refresh_tokens[rotated] = (subject, scope)
            body["refresh_token"] = rotated
        return httpx.Response(200, json=body)


def current_fake_oauth_provider() -> FakeOAuthProvider:
    """The fake OAuth provider of CHOREGOS_FAKES=1: one, scripted by the tests and the demo."""
    import choregos_adapters as package

    if package.FAKE_OAUTH is None:
        package.FAKE_OAUTH = FakeOAuthProvider()
    found: FakeOAuthProvider = package.FAKE_OAUTH
    return found
