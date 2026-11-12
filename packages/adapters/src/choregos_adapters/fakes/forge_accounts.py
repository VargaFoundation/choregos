# SPDX-License-Identifier: Apache-2.0
"""A scriptable fake forge (ADR 0005): the OAuth 2.0 sign-in and the `/user` read of GitHub, GitLab and
Forgejo, in memory (ADR 0154).

- `authorize(url, login)` plays the person signing in to the forge in their browser: it checks the
  authorization request (client, redirect URI, `response_type=code`, PKCE `S256`, the provider's scope) and
  answers the redirect the browser would follow, `redirect_uri?code=...&state=...`;
- the token endpoint (`/login/oauth/access_token`, or `/oauth/token` for GitLab) takes the
  `authorization_code` grant: the PKCE verifier must match the challenge, the redirect URI the one
  authorized, the client secret the application's;
- the user endpoint (`api.github.com/user`, `/api/v4/user`, `/api/v1/user`) answers the user behind the
  bearer token, in the provider's own shape.

Any host is served: the fake is one transport for every instance a test declares. Every request is recorded.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from ..forge_accounts import SCOPES


@dataclass(frozen=True, slots=True)
class FakeForgeUser:
    id: int
    login: str


@dataclass
class FakeForge:
    client_id: str = "choregos-forge-client"
    client_secret: str = "forge-secret"
    #: login -> user; `authorize` adds an unknown login with the next id
    users: dict[str, FakeForgeUser] = field(default_factory=dict)
    #: code -> (challenge, redirect URI, login)
    codes: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    #: access token -> login
    tokens: dict[str, str] = field(default_factory=dict)
    requests: list[httpx.Request] = field(default_factory=list)
    issued: int = 0

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.respond)

    def user(self, login: str, user_id: int | None = None) -> FakeForgeUser:
        if login not in self.users:
            self.users[login] = FakeForgeUser(user_id or 1000 + len(self.users) + 1, login)
        return self.users[login]

    def authorize(self, url: str, login: str = "octo") -> str:
        """The person signs in and consents: the redirect the browser follows, or a `ValueError`."""
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        if query.get("client_id") != self.client_id:
            raise ValueError("unknown client_id")
        if query.get("response_type") != "code":
            raise ValueError("response_type must be code")
        if query.get("code_challenge_method") != "S256" or not query.get("code_challenge"):
            raise ValueError("PKCE S256 is required")
        if query.get("scope") not in set(SCOPES.values()):
            raise ValueError(f"unexpected scope {query.get('scope')!r}")
        redirect = query.get("redirect_uri", "")
        if not redirect:
            raise ValueError("redirect_uri is required")
        self.user(login)
        self.issued += 1
        code = f"code-{self.issued}"
        self.codes[code] = (query["code_challenge"], redirect, login)
        return f"{redirect}?{urlencode({'code': code, 'state': query.get('state', '')})}"

    def respond(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith(("/login/oauth/access_token", "/oauth/token")):
            return self._token(request)
        if request.method == "GET" and path.endswith("/user"):
            return self._user(request, path)
        return httpx.Response(404, json={"message": "Not Found"})

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        if form.get("client_id") != self.client_id or form.get("client_secret") != self.client_secret:
            return httpx.Response(401, json={"error": "invalid_client"})
        if form.get("grant_type") != "authorization_code":
            return httpx.Response(400, json={"error": "unsupported_grant_type"})
        issued = self.codes.pop(form.get("code", ""), None)
        if issued is None:
            # GitHub's own shape for a refused code: 200 and an `error` field.
            return httpx.Response(200, json={"error": "bad_verification_code"})
        challenge, redirect, login = issued
        digest = hashlib.sha256(form.get("code_verifier", "").encode("ascii")).digest()
        if base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=") != challenge:
            return httpx.Response(400, json={"error": "invalid_grant"})
        if form.get("redirect_uri") != redirect:
            return httpx.Response(400, json={"error": "invalid_grant"})
        self.issued += 1
        token = f"forge-token-of-{login}-{self.issued}"
        self.tokens[token] = login
        return httpx.Response(200, json={"access_token": token, "token_type": "bearer"})

    def _user(self, request: httpx.Request, path: str) -> httpx.Response:
        bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
        login = self.tokens.get(bearer)
        if login is None:
            return httpx.Response(401, json={"message": "Bad credentials"})
        user = self.users[login]
        site = f"{request.url.scheme}://{request.url.host}"
        avatar = f"{site}/avatars/{user.id}"
        body: dict[str, Any]
        if path.endswith("/api/v4/user"):
            body = {
                "id": user.id,
                "username": user.login,
                "avatar_url": avatar,
                "web_url": f"{site}/{user.login}",
            }
        else:
            profile = "https://github.com" if request.url.host == "api.github.com" else site
            body = {
                "id": user.id,
                "login": user.login,
                "avatar_url": avatar,
                "html_url": f"{profile}/{user.login}",
            }
        return httpx.Response(200, json=body)


_CURRENT: list[FakeForge] = []


def current_fake_forge() -> FakeForge:
    """The fake forge of CHOREGOS_FAKES=1: one, scripted by the tests and the demo."""
    if not _CURRENT:
        _CURRENT.append(FakeForge())
    return _CURRENT[0]


def reset_fake_forge() -> FakeForge:
    _CURRENT.clear()
    return current_fake_forge()
