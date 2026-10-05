# SPDX-License-Identifier: Apache-2.0
"""Microsoft Entra ID par Microsoft Graph (ADR 0034, S20-03) : les gestes d'une arrivée et d'un
départ — créer un compte, l'ajouter à des groupes, le désactiver, révoquer ses sessions.

Chaque geste est IDEMPOTENT, parce qu'une action gouvernée se rejoue (ADR 0035) :
- `create_user` cherche d'abord par UPN : un compte qui existe est rendu, pas recréé ;
- `add_to_group` tient « already exist » pour un succès, `remove_from_group` un 404 aussi ;
- `disable_user` et `revoke_sessions` sont sans effet la seconde fois.

Une UNITÉ ADMINISTRATIVE borne ce que la plateforme touche : un compte créé y entre, et un geste
sur un compte qui n'y est pas est refusé, nommé — la défense en profondeur d'un rôle Graph déjà
délimité à l'unité. `Retry-After` est respecté (`RestClient`).
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from ..errors import AdapterError, UpstreamError
from ..http.rest import RestClient


def _schema(*requis: str, **autres: dict[str, Any]) -> dict[str, Any]:
    proprietes: dict[str, Any] = {nom: {"type": "string"} for nom in requis}
    proprietes.update(autres)
    return {
        "type": "object",
        "properties": proprietes,
        "required": list(requis),
        "additionalProperties": False,
    }


_UPN = _schema("upn")
_GROUPE = _schema("upn", "group_id")

#: (nom, accès, description, schéma d'entrée) — ce que le type déclare au registre.
OPERATIONS_ENTRA: tuple[tuple[str, str, str, dict[str, Any]], ...] = (
    ("get_user", "read", "reads an account by its UPN", _UPN),
    (
        "create_user",
        "write",
        "creates an account in the administrative unit (idempotent by UPN)",
        _schema("upn", "display_name", mail_nickname={"type": "string"}),
    ),
    ("add_to_group", "write", "adds an account to a group (already a member: done)", _GROUPE),
    ("remove_from_group", "write", "removes an account from a group (not a member: done)", _GROUPE),
    ("disable_user", "write", "disables an account", _UPN),
    ("revoke_sessions", "write", "revokes every session of an account", _UPN),
)


class HorsDeLUnite(AdapterError):  # noqa: N818 - un refus nommé, pas une panne
    """Un geste sur un compte que l'unité administrative de la plateforme ne contient pas."""


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
        self.unite = administrative_unit_id
        self.graph_url = graph_url.rstrip("/")
        self.login_url = login_url.rstrip("/")
        self._http = httpx.AsyncClient(timeout=20.0, transport=transport)
        self._jeton: tuple[str, float] | None = None

    async def _graph(self) -> RestClient:
        if self._jeton is None or self._jeton[1] < time.monotonic() + 60:
            reponse = await self._http.post(
                f"{self.login_url}/{self.tenant_id}/oauth2/v2.0/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._secret,
                    "scope": "https://graph.microsoft.com/.default",
                },
            )
            if reponse.status_code >= 400:
                raise UpstreamError(
                    "entra", f"jeton refusé ({reponse.status_code})", status_code=reponse.status_code
                )
            corps = reponse.json()
            self._jeton = (
                str(corps["access_token"]),
                time.monotonic() + float(corps.get("expires_in", 3600)),
            )
        return RestClient(
            self.graph_url,
            headers={"Authorization": f"Bearer {self._jeton[0]}", "Content-Type": "application/json"},
            client=self._http,
            service="entra",
        )

    async def get_user(self, upn: str) -> dict[str, Any] | None:
        graph = await self._graph()
        try:
            return dict(await graph.request("GET", f"/users/{upn}"))
        except UpstreamError as erreur:
            if erreur.status_code == 404:
                return None
            raise

    async def _dans_l_unite(self, user_id: str) -> None:
        if not self.unite:
            return
        graph = await self._graph()
        unites = await graph.request("GET", f"/users/{user_id}/memberOf/microsoft.graph.administrativeUnit")
        if not any(u.get("id") == self.unite for u in (unites or {}).get("value", [])):
            raise HorsDeLUnite(f"le compte {user_id} n'est pas dans l'unité administrative {self.unite}")

    async def _existant(self, upn: str) -> dict[str, Any]:
        compte = await self.get_user(upn)
        if compte is None:
            raise AdapterError(f"aucun compte {upn}")
        await self._dans_l_unite(str(compte["id"]))
        return compte

    async def create_user(
        self, upn: str, display_name: str, mail_nickname: str | None = None, **_: Any
    ) -> dict[str, Any]:
        existant = await self.get_user(upn)
        if existant is not None:
            await self._dans_l_unite(str(existant["id"]))
            return {"id": existant["id"], "upn": upn, "created": False}
        graph = await self._graph()
        cree = await graph.request(
            "POST",
            "/users",
            json={
                "accountEnabled": True,
                "displayName": display_name,
                "mailNickname": mail_nickname or upn.split("@", maxsplit=1)[0],
                "userPrincipalName": upn,
                # Un mot de passe que personne ne connaît : l'arrivant le définit à sa première
                # connexion (ou l'authentification sans mot de passe le remplace).
                "passwordProfile": {
                    "forceChangePasswordNextSignIn": True,
                    "password": _mot_de_passe_jetable(),
                },
            },
        )
        if self.unite:
            await graph.request(
                "POST",
                f"/directory/administrativeUnits/{self.unite}/members/$ref",
                json={"@odata.id": f"{self.graph_url}/directoryObjects/{cree['id']}"},
            )
        return {"id": cree["id"], "upn": upn, "created": True}

    async def add_to_group(self, upn: str, group_id: str, **_: Any) -> dict[str, Any]:
        compte = await self._existant(upn)
        graph = await self._graph()
        try:
            await graph.request(
                "POST",
                f"/groups/{group_id}/members/$ref",
                json={"@odata.id": f"{self.graph_url}/directoryObjects/{compte['id']}"},
            )
        except UpstreamError as erreur:
            if erreur.status_code == 400 and "already exist" in str(erreur):
                return {"upn": upn, "group_id": group_id, "added": False}
            raise
        return {"upn": upn, "group_id": group_id, "added": True}

    async def remove_from_group(self, upn: str, group_id: str, **_: Any) -> dict[str, Any]:
        compte = await self._existant(upn)
        graph = await self._graph()
        try:
            await graph.request("DELETE", f"/groups/{group_id}/members/{compte['id']}/$ref")
        except UpstreamError as erreur:
            if erreur.status_code == 404:
                return {"upn": upn, "group_id": group_id, "removed": False}
            raise
        return {"upn": upn, "group_id": group_id, "removed": True}

    async def disable_user(self, upn: str, **_: Any) -> dict[str, Any]:
        compte = await self._existant(upn)
        graph = await self._graph()
        await graph.request("PATCH", f"/users/{compte['id']}", json={"accountEnabled": False})
        return {"upn": upn, "enabled": False}

    async def revoke_sessions(self, upn: str, **_: Any) -> dict[str, Any]:
        compte = await self._existant(upn)
        graph = await self._graph()
        await graph.request("POST", f"/users/{compte['id']}/revokeSignInSessions")
        return {"upn": upn, "revoked": True}

    async def executer(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        """Une opération déclarée, par son nom : ce qu'un effet ou le courtier appelle."""
        if operation not in {nom for nom, *_ in OPERATIONS_ENTRA}:
            raise AdapterError(f"entra n'a pas d'opération {operation}")
        if operation == "get_user":
            return {"user": await self.get_user(str(params["upn"]))}
        resultat: dict[str, Any] = await getattr(self, operation)(**params)
        return resultat

    async def test(self) -> dict[str, Any]:
        await self._graph()
        return {"ok": True, "tenant": self.tenant_id, "administrative_unit": self.unite}


def _mot_de_passe_jetable() -> str:
    import secrets

    return "C" + secrets.token_urlsafe(24) + "!7a"
