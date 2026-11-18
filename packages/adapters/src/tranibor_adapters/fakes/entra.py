# SPDX-License-Identifier: Apache-2.0
"""A scriptable fake Microsoft Graph (ADR 0005): accounts, groups, administrative unit, and a 429
on demand — what the `entra` connector must hold without the real tenant."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
ALREADY_EXISTS = "Another object with the same value for property userPrincipalName already exists."
ALREADY_MEMBER = (
    "One or more added object references already exist for the following modified properties: 'members'."
)


@dataclass
class FakeEntra:
    unit: str = "au-plateforme"
    accounts: dict[str, dict[str, Any]] = field(default_factory=dict)
    groups: dict[str, set[str]] = field(default_factory=dict)
    unit_members: set[str] = field(default_factory=set)
    revoked_sessions: list[str] = field(default_factory=list)
    #: how many 429 to return before answering
    throttled_requests: int = 0
    #: the application secret expected at the token endpoint; empty: any
    expected_secret: str = field(default="", repr=False)
    received: list[tuple[str, str]] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.respond)

    def add_account(self, upn: str, *, in_unit: bool = True) -> str:
        identifier = str(uuid.uuid4())
        self.accounts[identifier] = {"id": identifier, "userPrincipalName": upn, "accountEnabled": True}
        if in_unit:
            self.unit_members.add(identifier)
        return identifier

    def _account(self, key: str) -> dict[str, Any] | None:
        return self.accounts.get(key) or next(
            (a for a in self.accounts.values() if a["userPrincipalName"].lower() == key.lower()), None
        )

    def respond(self, request: httpx.Request) -> httpx.Response:  # noqa: C901 - a router
        path = request.url.path
        self.received.append((request.method, path))
        if path.endswith("/oauth2/v2.0/token"):
            secret = parse_qs(request.content.decode()).get("client_secret", [""])[0]
            if self.expected_secret and secret != self.expected_secret:
                return httpx.Response(401, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "graph-token", "expires_in": 3600})
        if request.headers.get("authorization") != "Bearer graph-token":
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        if self.throttled_requests > 0:
            self.throttled_requests -= 1
            return httpx.Response(
                429, headers={"Retry-After": "0"}, json={"error": {"code": "TooManyRequests"}}
            )
        body = json.loads(request.content or b"{}")
        path = path.removeprefix("/v1.0")
        if m := re.fullmatch(r"/users/([^/]+)/memberOf/microsoft.graph.administrativeUnit", path):
            account = self._account(m.group(1))
            inside = account is not None and account["id"] in self.unit_members
            return httpx.Response(200, json={"value": [{"id": self.unit}] if inside else []})
        if m := re.fullmatch(r"/users/([^/]+)/revokeSignInSessions", path):
            account = self._account(m.group(1))
            if account is None:
                return httpx.Response(404)
            self.revoked_sessions.append(account["userPrincipalName"])
            return httpx.Response(200, json={"value": True})
        if m := re.fullmatch(r"/users/([^/]+)", path):
            account = self._account(m.group(1))
            if account is None:
                return httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}})
            if request.method == "PATCH":
                account.update(body)
                return httpx.Response(204)
            return httpx.Response(200, json=account)
        if path == "/users" and request.method == "POST":
            if self._account(body["userPrincipalName"]) is not None:
                return httpx.Response(
                    400,
                    json={"error": {"message": ALREADY_EXISTS}},
                )
            identifier = str(uuid.uuid4())
            self.accounts[identifier] = {"id": identifier, **body}
            return httpx.Response(201, json=self.accounts[identifier])
        if m := re.fullmatch(r"/directory/administrativeUnits/([^/]+)/members/\$ref", path):
            self.unit_members.add(body["@odata.id"].rsplit("/", 1)[-1])
            return httpx.Response(204)
        if m := re.fullmatch(r"/groups/([^/]+)/members/\$ref", path):
            members = self.groups.setdefault(m.group(1), set())
            identifier = body["@odata.id"].rsplit("/", 1)[-1]
            if identifier in members:
                return httpx.Response(400, json={"error": {"message": ALREADY_MEMBER}})
            members.add(identifier)
            return httpx.Response(204)
        if m := re.fullmatch(r"/groups/([^/]+)/members/([^/]+)/\$ref", path):
            members = self.groups.setdefault(m.group(1), set())
            if m.group(2) not in members:
                return httpx.Response(404)
            members.discard(m.group(2))
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"code": "not in the fake", "path": path}})


#: E4's leaver and the groups they belong to (the examples page of PLAN-2026-11-03-tool-scripts).
OFFBOARDING_UPN = "j.doe@example.org"
OFFBOARDING_GROUPS = ("G-FIN", "G-VPN", "G-SHAREPOINT", "G-CRM", "G-BI", "G-ALL")


def offboarding_directory() -> FakeEntra:
    """E4 (S57-09): an enabled account in the platform's unit, member of six groups, with sessions to
    revoke. An offboarding under approval must leave it exactly so: a test compares `state()`."""
    fake = FakeEntra()
    identifier = fake.add_account(OFFBOARDING_UPN)
    for group in OFFBOARDING_GROUPS:
        fake.groups[group] = {identifier}
    return fake


def state(fake: FakeEntra) -> dict[str, Any]:
    """What a write would change: the accounts, the groups' members, the revoked sessions."""
    return {
        "accounts": {key: dict(value) for key, value in fake.accounts.items()},
        "groups": {key: sorted(value) for key, value in fake.groups.items()},
        "revoked_sessions": list(fake.revoked_sessions),
    }
