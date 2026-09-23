"""Passerelle « directe » : il n'y en a pas.

Le modèle par défaut de Choregos est une passerelle qui frappe **une clé virtuelle par run**,
plafonnée, et qui compte la dépense à la source. Deux situations n'entrent pas dans ce moule :

- l'agent apporte ses propres identifiants (un abonnement Claude Code, par exemple) — la
  plateforme n'a alors aucune clé à émettre, et ne doit surtout pas en écrire une vide ;
- un déploiement d'essai parle à un fournisseur avec une clé unique, posée dans le pod.

Ce qu'on perd, et qu'il faut savoir : **aucun plafond de dépense, aucun coût mesuré**. Le budget
d'une étape se limite alors à ce que l'orchestrateur sait encore tenir — tours et minutes.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from choregos_core.domain import GatewayModel, Spend, VirtualKey


class DirectGateway:
    """Rend la clé qu'on lui a donnée, ou rien du tout."""

    def __init__(self, key: str = "", *, models: list[str] | None = None) -> None:
        self.key = key
        self.models = list(models or [])

    async def mint_key(
        self, metadata: dict[str, Any], budget_usd: float, ttl_s: int, models: list[str]
    ) -> VirtualKey:
        # L'identifiant doit être UNIQUE par run : la plateforme range chaque clé dans une
        # table où `key_id` porte un index unique. Un identifiant constant passait au premier
        # run et faisait échouer le second sur une violation de contrainte — un message qui
        # parle de base de données là où le fautif est le connecteur.
        run = str(metadata.get("run_id") or uuid4())
        return VirtualKey(
            key=self.key,
            key_id=f"direct:{run}",
            budget_usd=budget_usd,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_s),
            models=models,
            metadata=dict(metadata),
        )

    async def spend(self, key_id: str) -> Spend:
        # Zéro n'est pas « gratuit » : c'est « non mesuré ». Le tableau des coûts le montrera
        # à zéro, et c'est la conséquence assumée de se passer de passerelle.
        return Spend()

    async def revoke(self, key_id: str) -> None:
        return None

    async def list_models(self) -> list[GatewayModel]:
        return [GatewayModel(model_name=name, litellm_model=name) for name in self.models]
