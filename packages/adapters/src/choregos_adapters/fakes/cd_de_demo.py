# SPDX-License-Identifier: Apache-2.0
"""Un CD de démonstration (S21-24) : ce que le train d'un locataire de démonstration promeut, sans
Argo CD.

Le projet de développement du locataire dev (S21-25) prend un VRAI train : promotion, soak,
vérification, notification des tickets (ADR 0041). Il lui faut un `cd` qui réponde. Le faux `FakeCd`
vit dans le processus qui l'a construit — l'orchestrateur promouvrait une application que l'API ne
verrait pas. Le type `cd: demo` tient donc le même état que les familles métier :

- sans `url`, en mémoire, partagé dans le processus ;
- avec une `url`, servi par le processus des faux (`fakes/serveur.py`, `/cd/mcp`) : l'API et
  l'orchestrateur voient le même environnement.

Refusé en staging et en prod (comme les familles métier) : une démonstration, pas un déploiement.
Chaque écriture est idempotente — une activité Temporal se rejoue (ADR 0008).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from choregos_core.domain import Change, Health, PromotionRef, RolloutState, Window

from ..registry import OperationSpec, schema_d_entree
from .rh import FauxMetier

_LISTE_D_OBJETS: dict[str, Any] = {"type": "array", "items": {"type": "object"}}

OPERATIONS_CD_DE_DEMO: tuple[OperationSpec, ...] = (
    OperationSpec(
        "promote",
        "write",
        "promotes a release's changes to an environment (the same release twice: done)",
        schema_d_entree("env", "release", changes=_LISTE_D_OBJETS),
    ),
    OperationSpec("health", "read", "reads an application's health and revision", schema_d_entree("app")),
    OperationSpec("rollout_status", "read", "reads an application's rollout", schema_d_entree("app")),
    OperationSpec("abort_rollout", "write", "aborts an application's rollout", schema_d_entree("app")),
    OperationSpec("current_revision", "read", "reads an application's revision", schema_d_entree("app")),
    OperationSpec(
        "set_sync_window",
        "write",
        "sets an application's sync windows",
        schema_d_entree("app", windows=_LISTE_D_OBJETS),
    ),
    OperationSpec(
        "break_next_rollout",
        "write",
        "makes the next rollout analysis fail, to show a rollback",
        schema_d_entree(),
    ),
)


@dataclass
class FakeCdDeDemo(FauxMetier):
    """Un environnement de démonstration : une application promue est saine, à la révision promue."""

    operations = OPERATIONS_CD_DE_DEMO
    applications: dict[str, dict[str, Any]] = field(default_factory=dict)
    promotions: list[dict[str, Any]] = field(default_factory=list)
    fenetres: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    casser_le_prochain: bool = False

    def promote(self, env: str, release: str, changes: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        for deja in self.promotions:
            if (deja["env"], deja["release"]) == (env, release):
                return {**deja, "promoted": False}
        promotion = {
            "env": env,
            "release": release,
            "number": len(self.promotions) + 1,
            "apps": [str(c["app"]) for c in changes or []],
        }
        # Une promotion cassée exprès (`break_next_rollout`) arrive dégradée : le train le voit à son
        # analyse, et revient en arrière. Une lecture ne change rien ; seules les écritures écrivent.
        etat = "Degraded" if self.casser_le_prochain else "Healthy"
        self.casser_le_prochain = False
        for change in changes or []:
            revision = str(change.get("tag") or change.get("image") or release)
            self.applications[str(change["app"])] = {"status": etat, "revision": revision, "rollout": etat}
        self.promotions.append(promotion)
        return {**promotion, "promoted": True}

    def health(self, app: str) -> dict[str, Any]:
        etat = self.applications.get(app)
        if etat is None:
            return {"status": "Missing", "message": f"{app} has never been promoted here"}
        return {"status": etat["status"], "revision": etat["revision"]}

    def rollout_status(self, app: str) -> dict[str, Any]:
        etat = self.applications.get(app)
        if etat is None:
            return {"phase": "Unknown", "message": f"{app} has never been promoted here"}
        message = "demo: analysis failed on purpose" if etat["rollout"] == "Degraded" else ""
        return {
            "phase": etat["rollout"],
            "current_step": 1,
            "total_steps": 1,
            "canary_weight": 100,
            "message": message,
        }

    def abort_rollout(self, app: str) -> dict[str, Any]:
        etat = self.applications.get(app)
        if etat is not None:
            etat["rollout"] = "Aborted"
        return {"app": app, "phase": "Aborted"}

    def current_revision(self, app: str) -> dict[str, Any]:
        return {"app": app, "revision": (self.applications.get(app) or {}).get("revision") or "unknown"}

    def set_sync_window(self, app: str, windows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        self.fenetres[app] = list(windows or [])
        return {"app": app, "windows": len(self.fenetres[app])}

    def break_next_rollout(self) -> dict[str, Any]:
        self.casser_le_prochain = True
        return {"armed": True}


@dataclass
class CdDeDemo:
    """L'adaptateur `cd` (le protocole du train) sur un guichet : local (`Guichet`) ou servi
    (`GuichetDistant`). Les objets du domaine passent en JSON, et en reviennent."""

    guichet: Any

    async def current_revision(self, app: str) -> str:
        return str((await self.guichet.executer("current_revision", {"app": app}))["revision"])

    async def promote(self, env: str, changes: list[Change], release: str) -> PromotionRef:
        listes = [c.model_dump(mode="json", exclude_none=True) for c in changes]
        fait = await self.guichet.executer("promote", {"env": env, "release": release, "changes": listes})
        return PromotionRef(kind="commit", url=f"demo://{env}/{fait['number']}", ref=release, merged=True)

    async def health(self, app: str) -> Health:
        return Health.model_validate(await self.guichet.executer("health", {"app": app}))

    async def rollout_status(self, app: str) -> RolloutState:
        return RolloutState.model_validate(await self.guichet.executer("rollout_status", {"app": app}))

    async def abort_rollout(self, app: str) -> None:
        await self.guichet.executer("abort_rollout", {"app": app})

    async def set_sync_window(self, app: str, windows: list[Window]) -> None:
        fenetres = [w.model_dump(mode="json") for w in windows]
        await self.guichet.executer("set_sync_window", {"app": app, "windows": fenetres})

    async def test(self) -> dict[str, Any]:
        resultat: dict[str, Any] = await self.guichet.test()
        return resultat


__all__ = ["OPERATIONS_CD_DE_DEMO", "CdDeDemo", "FakeCdDeDemo"]
