# SPDX-License-Identifier: Apache-2.0
"""Les faux du scénario RH (ADR 0005, S20-04) : un gestionnaire de parc, un transporteur, des
lecteurs de badges — et l'agent du fournisseur de postes.

Chacun tient les opérations de sa famille (`familles.py`) comme un vrai système devrait les tenir :
- chaque écriture est IDEMPOTENTE, parce qu'une action gouvernée se rejoue (ADR 0035) : inscrire
  deux fois le même poste donne une inscription, commander deux fois la même référence une
  commande ;
- une écriture qui CONTREDIT l'état est refusée, nommée : un poste inscrit pour un autre, une
  référence déjà prise par un autre envoi, un badge inconnu qu'on croit couper ;
- la clé de l'instance est vérifiée à chaque appel, quand le faux en attend une.

Un même faux se joint de deux façons : typé, par un connecteur `demo` (`Guichet`), ou servi comme
un serveur MCP (`serveur_mcp`), ce que joint un connecteur `mcp`. L'agent du fournisseur n'existe
que sous la seconde : c'est un tiers, ses outils se découvrent.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import inspect
from dataclasses import dataclass, field
from functools import partial
from typing import Any, ClassVar

from ..errors import AdapterError, UpstreamError
from ..familles import OPERATIONS_CONTROLE_D_ACCES, OPERATIONS_EXPEDITION, OPERATIONS_MDM
from ..registry import OperationSpec, schema_d_entree
from .mcp import FakeMcpServer


def _empreinte(reference: str) -> str:
    """Un numéro tiré de la référence, pas d'un compteur : un geste rejoué rend le même."""
    return hashlib.sha256(reference.encode()).hexdigest()[:8].upper()


@dataclass
class FauxMetier:
    """Ce que les faux du scénario partagent : des opérations déclarées, appelées par leur nom, et
    la clé qu'ils attendent (vide : aucune)."""

    operations: ClassVar[tuple[OperationSpec, ...]] = ()
    cle_attendue: str = field(default="", repr=False, kw_only=True)

    def authentifier(self, cle: str) -> None:
        if self.cle_attendue and not hmac.compare_digest(cle.encode(), self.cle_attendue.encode()):
            raise UpstreamError(type(self).__name__, "clé refusée", status_code=401)

    def faire(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        if operation not in {o.name for o in self.operations}:
            raise AdapterError(f"{type(self).__name__} n'a pas d'opération {operation}")
        methode = getattr(self, operation)
        try:
            inspect.signature(methode).bind(**params)
        except TypeError as erreur:
            raise AdapterError(f"{operation} : {erreur}") from erreur
        return dict(methode(**params))

    def etat(self) -> dict[str, Any]:
        """L'état du système simulé, copié : ce qu'un geste rejoué ne doit pas changer."""
        return copy.deepcopy({cle: valeur for cle, valeur in vars(self).items() if cle != "cle_attendue"})


@dataclass
class FakeMdm(FauxMetier):
    operations = OPERATIONS_MDM
    postes: dict[str, dict[str, Any]] = field(default_factory=dict)

    def enroll_device(self, serial: str, upn: str) -> dict[str, Any]:
        poste = self.postes.get(serial)
        if poste is not None and poste["state"] == "enrolled":
            if poste["upn"] != upn:
                raise AdapterError(f"le poste {serial} est inscrit pour un autre utilisateur")
            return {**poste, "enrolled": False}
        self.postes[serial] = {"serial": serial, "upn": upn, "state": "enrolled"}
        return {**self.postes[serial], "enrolled": True}

    def wipe_device(self, serial: str) -> dict[str, Any]:
        poste = self.postes.get(serial)
        if poste is None:
            raise AdapterError(f"aucun poste {serial} au parc")
        efface = poste["state"] != "wiped"
        # Effacé, le poste est libéré : il pourra être inscrit pour un arrivant.
        poste.update(state="wiped", upn=None)
        return {**poste, "wiped": efface}

    def device_status(self, serial: str) -> dict[str, Any]:
        return dict(self.postes.get(serial) or {"serial": serial, "upn": None, "state": "unknown"})


@dataclass
class FakeTransporteur(FauxMetier):
    operations = OPERATIONS_EXPEDITION
    envois: dict[str, dict[str, Any]] = field(default_factory=dict)

    def _envoi(self, reference: str, address: str, sens: str, items: list[str]) -> dict[str, Any]:
        deja = self.envois.get(reference)
        if deja is not None:
            if (deja["direction"], deja["address"]) != (sens, address):
                raise AdapterError(f"la référence {reference} est déjà prise par un autre envoi")
            return {**deja, "created": False}
        aller = sens == "outbound"
        self.envois[reference] = {
            "reference": reference,
            "direction": sens,
            "address": address,
            "items": list(items),
            "tracking": f"{'TRK' if aller else 'RET'}-{_empreinte(reference)}",
            "state": "shipped" if aller else "collection_booked",
        }
        return {**self.envois[reference], "created": True}

    def create_shipment(self, reference: str, address: str, items: list[str] | None = None) -> dict[str, Any]:
        return self._envoi(reference, address, "outbound", items or [])

    def create_return(self, reference: str, address: str) -> dict[str, Any]:
        return self._envoi(reference, address, "return", [])

    def shipment_status(self, reference: str) -> dict[str, Any]:
        return dict(self.envois.get(reference) or {"reference": reference, "state": "unknown"})

    def livrer(self, reference: str) -> None:
        """Mise en scène : le colis est arrivé, ou repris."""
        envoi = self.envois[reference]
        envoi["state"] = "delivered" if envoi["direction"] == "outbound" else "collected"


@dataclass
class FakeBadges(FauxMetier):
    operations = OPERATIONS_CONTROLE_D_ACCES
    badges: dict[str, dict[str, Any]] = field(default_factory=dict)

    def activate_badge(self, uid: str, holder: str) -> dict[str, Any]:
        badge = self.badges.get(uid)
        if badge is not None and badge["active"]:
            if badge["holder"] != holder:
                raise AdapterError(f"le badge {uid} est actif pour un autre porteur")
            return {**badge, "activated": False}
        self.badges[uid] = {"uid": uid, "holder": holder, "active": True}
        return {**self.badges[uid], "activated": True}

    def deactivate_badge(self, uid: str) -> dict[str, Any]:
        badge = self.badges.get(uid)
        if badge is None:
            # Pas un succès : un UID mal saisi laisserait le vrai badge ouvrir les portes.
            raise AdapterError(f"aucun badge {uid}")
        coupe = bool(badge["active"])
        badge["active"] = False
        return {**badge, "deactivated": coupe}

    def badge_status(self, uid: str) -> dict[str, Any]:
        return dict(self.badges.get(uid) or {"uid": uid, "holder": None, "active": False})


#: Les outils de l'agent du fournisseur. Il les annonce lui-même, en MCP : la plateforme les
#: découvre, et ils naissent fermés (ADR 0034).
OUTILS_DU_FOURNISSEUR: tuple[OperationSpec, ...] = (
    OperationSpec(
        "commander_poste",
        "write",
        "commande un poste pour un arrivant ; une commande par référence",
        schema_d_entree("reference", "modele", "upn"),
    ),
    OperationSpec(
        "suivi_commande",
        "read",
        "où en est une commande : son état, le numéro de série du poste, le suivi du colis",
        schema_d_entree("reference"),
    ),
)


@dataclass
class FakeFournisseur(FauxMetier):
    operations = OUTILS_DU_FOURNISSEUR
    commandes: dict[str, dict[str, Any]] = field(default_factory=dict)

    def commander_poste(self, reference: str, modele: str, upn: str) -> dict[str, Any]:
        deja = self.commandes.get(reference)
        if deja is not None:
            if (deja["modele"], deja["upn"]) != (modele, upn):
                raise AdapterError(f"la commande {reference} existe déjà, pour un autre poste")
            return {**deja, "created": False}
        self.commandes[reference] = {
            "reference": reference,
            "modele": modele,
            "upn": upn,
            "serial": f"PC-{_empreinte(reference)}",
            "state": "confirmed",
            "tracking": None,
        }
        return {**self.commandes[reference], "created": True}

    def suivi_commande(self, reference: str) -> dict[str, Any]:
        return dict(self.commandes.get(reference) or {"reference": reference, "state": "unknown"})

    def expedier(self, reference: str) -> None:
        """Mise en scène : le fournisseur expédie le poste."""
        self.commandes[reference].update(state="shipped", tracking=f"COLIS-{_empreinte(reference)}")


#: Le faux de chaque famille, par sorte de connecteur.
FAUX_PAR_FAMILLE: dict[str, type[FauxMetier]] = {
    "mdm": FakeMdm,
    "shipping": FakeTransporteur,
    "access_control": FakeBadges,
}


@dataclass
class Guichet:
    """Ce que construit un connecteur `demo` : le faux de sa famille, partagé dans le processus, et
    la clé de SON instance — résolue par la plateforme, présentée à chaque appel, jamais rendue."""

    faux: FauxMetier
    cle: str = field(default="", repr=False)

    async def executer(self, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        self.faux.authentifier(self.cle)
        return self.faux.faire(operation, params)

    async def test(self) -> dict[str, Any]:
        self.faux.authentifier(self.cle)
        return {"ok": True, "fake": type(self.faux).__name__}


def serveur_mcp(faux: FauxMetier, *, jeton: str = "") -> FakeMcpServer:
    """Le même faux, servi comme un serveur MCP : chaque opération devient un outil (une lecture
    porte `readOnlyHint`) et chaque appel joue la même méthode, sur le même état."""
    return FakeMcpServer(
        outils=[
            {
                "name": operation.name,
                "description": operation.description,
                "inputSchema": operation.input_schema or {"type": "object"},
                **({"annotations": {"readOnlyHint": True}} if operation.access == "read" else {}),
            }
            for operation in faux.operations
        ],
        jeton=jeton,
        gestes={operation.name: partial(faux.faire, operation.name) for operation in faux.operations},
    )
