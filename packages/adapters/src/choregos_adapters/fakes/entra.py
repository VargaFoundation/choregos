# SPDX-License-Identifier: Apache-2.0
"""Un faux Microsoft Graph, scriptable (ADR 0005) : comptes, groupes, unité administrative, et un
429 à la demande — ce que le connecteur `entra` doit tenir sans le vrai locataire."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs

import httpx

GRAPH = "https://graph.microsoft.com/v1.0"
DEJA_LA = "Another object with the same value for property userPrincipalName already exists."
DEJA_MEMBRE = (
    "One or more added object references already exist for the following modified properties: 'members'."
)


@dataclass
class FakeEntra:
    unite: str = "au-plateforme"
    comptes: dict[str, dict[str, Any]] = field(default_factory=dict)
    groupes: dict[str, set[str]] = field(default_factory=dict)
    membres_de_l_unite: set[str] = field(default_factory=set)
    sessions_revoquees: list[str] = field(default_factory=list)
    #: combien de 429 rendre avant de répondre
    trop_de_requetes: int = 0
    #: le secret de l'application attendu au jeton ; vide : n'importe lequel
    secret_attendu: str = field(default="", repr=False)
    recues: list[tuple[str, str]] = field(default_factory=list)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.repondre)

    def ajouter_compte(self, upn: str, *, dans_l_unite: bool = True) -> str:
        identifiant = str(uuid.uuid4())
        self.comptes[identifiant] = {"id": identifiant, "userPrincipalName": upn, "accountEnabled": True}
        if dans_l_unite:
            self.membres_de_l_unite.add(identifiant)
        return identifiant

    def _compte(self, cle: str) -> dict[str, Any] | None:
        return self.comptes.get(cle) or next(
            (c for c in self.comptes.values() if c["userPrincipalName"].lower() == cle.lower()), None
        )

    def repondre(self, requete: httpx.Request) -> httpx.Response:  # noqa: C901 - un routeur
        chemin = requete.url.path
        self.recues.append((requete.method, chemin))
        if chemin.endswith("/oauth2/v2.0/token"):
            secret = parse_qs(requete.content.decode()).get("client_secret", [""])[0]
            if self.secret_attendu and secret != self.secret_attendu:
                return httpx.Response(401, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "jeton-graph", "expires_in": 3600})
        if requete.headers.get("authorization") != "Bearer jeton-graph":
            return httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}})
        if self.trop_de_requetes > 0:
            self.trop_de_requetes -= 1
            return httpx.Response(
                429, headers={"Retry-After": "0"}, json={"error": {"code": "TooManyRequests"}}
            )
        corps = json.loads(requete.content or b"{}")
        chemin = chemin.removeprefix("/v1.0")
        if m := re.fullmatch(r"/users/([^/]+)/memberOf/microsoft.graph.administrativeUnit", chemin):
            compte = self._compte(m.group(1))
            dedans = compte is not None and compte["id"] in self.membres_de_l_unite
            return httpx.Response(200, json={"value": [{"id": self.unite}] if dedans else []})
        if m := re.fullmatch(r"/users/([^/]+)/revokeSignInSessions", chemin):
            compte = self._compte(m.group(1))
            if compte is None:
                return httpx.Response(404)
            self.sessions_revoquees.append(compte["userPrincipalName"])
            return httpx.Response(200, json={"value": True})
        if m := re.fullmatch(r"/users/([^/]+)", chemin):
            compte = self._compte(m.group(1))
            if compte is None:
                return httpx.Response(404, json={"error": {"code": "Request_ResourceNotFound"}})
            if requete.method == "PATCH":
                compte.update(corps)
                return httpx.Response(204)
            return httpx.Response(200, json=compte)
        if chemin == "/users" and requete.method == "POST":
            if self._compte(corps["userPrincipalName"]) is not None:
                return httpx.Response(
                    400,
                    json={"error": {"message": DEJA_LA}},
                )
            identifiant = str(uuid.uuid4())
            self.comptes[identifiant] = {"id": identifiant, **corps}
            return httpx.Response(201, json=self.comptes[identifiant])
        if m := re.fullmatch(r"/directory/administrativeUnits/([^/]+)/members/\$ref", chemin):
            self.membres_de_l_unite.add(corps["@odata.id"].rsplit("/", 1)[-1])
            return httpx.Response(204)
        if m := re.fullmatch(r"/groups/([^/]+)/members/\$ref", chemin):
            membres = self.groupes.setdefault(m.group(1), set())
            identifiant = corps["@odata.id"].rsplit("/", 1)[-1]
            if identifiant in membres:
                return httpx.Response(400, json={"error": {"message": DEJA_MEMBRE}})
            membres.add(identifiant)
            return httpx.Response(204)
        if m := re.fullmatch(r"/groups/([^/]+)/members/([^/]+)/\$ref", chemin):
            membres = self.groupes.setdefault(m.group(1), set())
            if m.group(2) not in membres:
                return httpx.Response(404)
            membres.discard(m.group(2))
            return httpx.Response(204)
        return httpx.Response(404, json={"error": {"code": "pas dans le faux", "path": chemin}})
