# SPDX-License-Identifier: Apache-2.0
"""Microsoft Entra ID through Microsoft Graph (ADR 0034, S20-03): the gestures of a joiner and a
leaver — create an account, add it to groups, disable it, revoke its sessions.

Every gesture is IDEMPOTENT, because a governed action is replayed (ADR 0035):
- `create_user` looks up the UPN first: an account that exists is returned, not recreated;
- `add_to_group` takes "already exist" for a success, `remove_from_group` takes a 404 too;
- `disable_user` and `revoke_sessions` have no effect the second time.

An ADMINISTRATIVE UNIT bounds what the platform touches: an account it creates enters it, and a
gesture on an account that is not in it is refused, by name — the defence in depth of a Graph role
already scoped to the unit. `Retry-After` is honoured (`RestClient`).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..errors import AdapterError, UpstreamError
from ..http.rest import RestClient
from ..registry import schema_d_entree as _schema

_UPN = _schema("upn")
_GROUP = _schema("upn", "group_id")

#: (name, access, description, input schema) — what the type declares to the registry.
OPERATIONS_ENTRA: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("get_user", "read", "reads an account by its UPN", _UPN),
    (
        "create_user",
        "write",
        "creates an account in the administrative unit (idempotent by UPN)",
        _schema("upn", "display_name", mail_nickname={"type": "string"}),
    ),
    ("add_to_group", "write", "adds an account to a group (already a member: done)", _GROUP),
    ("remove_from_group", "write", "removes an account from a group (not a member: done)", _GROUP),
    ("disable_user", "write", "disables an account", _UPN),
    ("revoke_sessions", "write", "revokes every session of an account", _UPN),
)


class OutsideUnit(AdapterError):  # noqa: N818 - a named refusal, not a failure
    """A gesture on an account that the platform's administrative unit does not contain."""


class EntraIdentity:
    def __init__(
        self,
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        administrative_unit_id: str | None = None,
        graph_url: str = "https://graph.microsoft.com/v1.0",
        login_url: str = "https://login.microsoftonline.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self._client_id = client_id
        self._secret = client_secret
        self.unit = administrative_unit_id
        self.graph_url = graph_url.rstrip("/")
        self.login_url = login_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=20.0, transport=transport)
        self._token: tuple[str, float] | None = None

    @property
    def unite(self) -> str | None:  # lang: fr-ok alias:internal
        return self.unit

    async def _graph(self) -> RestClient:
        if self._token is None or self._token[1] < time.monotonic() + 60:
            response = await self._http.post(
                f"{self.login_url}/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            if response.status_code >= 400:
                raise UpstreamError(
                    "entra", f"token refused ({response.status_code})", status_code=response.status_code
                )
            body = response.json()
            self._token = (
                str(body["access_token"]),
                time.monotonic() + float(body.get("expires_in", 3600)),
            )
        return RestClient(
            self.graph_url,
            headers={"Authorization": f"Bearer {self._token[0]}", "Content-Type": "application/json"},
            client=self._http,
            service="entra",
        )

    async def get_user(self, upn: str) -> dict[str, Any] | None:
        graph = await self._graph()
        try:
            return dict(await graph.request("GET", f"/users/{upn}"))
        except UpstreamError as error:
            if error.status_code == 404:
                return None
            raise

    async def _in_unit(self, user_id: str) -> None:
        if not self.unit:
            return
        graph = await self._graph()
        units = await graph.request("GET", f"/users/{user_id}/memberOf/microsoft.graph.administrativeUnit")
        if not any(u.get("id") == self.unit for u in (units or {}).get("value", [])):
            raise OutsideUnit(f"account {user_id} is not in the administrative unit {self.unit}")

    async def _existing(self, upn: str) -> dict[str, Any]:
        account = await self.get_user(upn)
        if account is None:
            raise AdapterError(f"no account {upn}")
        await self._in_unit(str(account["id"]))
        return account

    async def create_user(
        self, upn: str, display_name: str, mail_nickname: str | None = None, **_: Any
    ) -> dict[str, Any]:
        existing = await self.get_user(upn)
        if existing is not None:
            await self._in_unit(str(existing["id"]))
            return {"id": existing["id"], "upn": upn, "created": False}
        graph = await self._graph()
        created = await graph.request(
            "POST",
            "/users",
            json={
                "accountEnabled": True,
                "displayName": display_name,
                "mailNickname": mail_nickname or upn.split("@", maxsplit=1)[0],
                "userPrincipalName": upn,
                # A password that nobody knows: the joiner sets it at the first sign-in (or
                # passwordless authentication replaces it).
                "passwordProfile": {
                    "forceChangePasswordNextSignIn": True,
                    "password": _throwaway_password(),
                },
            },
        )
        if self.unit:
            await graph.request(
                "POST",
                f"/directory/administrativeUnits/{self.unit}/members/$ref",
                json={"@odata.id": f"{self.graph_url}/directoryObjects/{created['id']}"},
            )
        return {"id": created["id"], "upn": upn, "created": True}

    async def add_to_group(self, upn: str, group_id: str, **_: Any) -> dict[str, Any]:
        account = await self._existing(upn)
        graph = await self._graph()
        try:
            await graph.request(
                "POST",
                f"/groups/{group_id}/members/$ref",
                json={"@odata.id": f"{self.graph_url}/directoryObjects/{account['id']}"},
            )
        except UpstreamError as error:
            if error.status_code == 400 and "already exist" in str(error):
                return {"upn": upn, "group_id": group_id, "added": False}
            raise
        return {"upn": upn, "group_id": group_id, "added": True}

    async def remove_from_group(self, upn: str, group_id: str, **_: Any) -> dict[str, Any]:
        account = await self._existing(upn)
        graph = await self._graph()
        try:
            await graph.request("DELETE", f"/groups/{group_id}/members/{account['id']}/$ref")
        except UpstreamError as error:
            if error.status_code == 404:
                return {"upn": upn, "group_id": group_id, "removed": False}
            raise
        return {"upn": upn, "group_id": group_id, "removed": True}

    async def disable_user(self, upn: str, **_: Any) -> dict[str, Any]:
        account = await self._existing(upn)
        graph = await self._graph()
        await graph.request("PATCH", f"/users/{account['id']}", json={"accountEnabled": False})
        return {"upn": upn, "enabled": False}

    async def revoke_sessions(self, upn: str, **_: Any) -> dict[str, Any]:
        account = await self._existing(upn)
        graph = await self._graph()
        await graph.request("POST", f"/users/{account['id']}/revokeSignInSessions")
        return {"upn": upn, "revoked": True}

    async def execute(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        """A declared operation, by name: what an effect or the broker calls."""
        if operation not in {name for name, *_ in OPERATIONS_ENTRA}:
            raise AdapterError(f"entra has no operation {operation}")
        if operation == "get_user":
            return {"user": await self.get_user(str(params["upn"]))}
        result: dict[str, Any] = await getattr(self, operation)(**params)
        return result

    executer = execute  # lang: fr-ok alias:internal

    async def test(self) -> dict[str, Any]:
        await self._graph()
        return {"ok": True, "tenant": self.tenant_id, "administrative_unit": self.unit}


def _throwaway_password() -> str:
    import secrets

    return "C" + secrets.token_urlsafe(24) + "!7a"


# Former names that other code imports (ADR 0098 §3 (b)).
HorsDeLUnite = OutsideUnit  # lang: fr-ok alias:internal
