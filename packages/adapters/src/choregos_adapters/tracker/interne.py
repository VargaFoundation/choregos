"""Tracker « interne » : il n'y en a pas d'autre, la plateforme tient le ticket.

Le modèle par défaut de Choregos est d'être **invité** chez un tracker existant : GitHub
Issues, Jira. L'état, le coût et les preuves y sont reflétés parce que c'est là que les
humains regardent. Deux situations ne rentrent pas dans ce moule :

- un métier dont les demandes n'existent nulle part ailleurs — un besoin de staffing, un
  dossier à instruire — et pour qui le board de Choregos EST l'endroit où l'on regarde ;
- un banc de démonstration, qui n'a ni organisation GitHub ni site Jira.

Ce connecteur rend alors des non-opérations franches plutôt que des erreurs : rien à
refléter, rien à commenter dehors. Ce qu'on perd : l'aller-retour avec un outil tiers —
personne n'est prévenu ailleurs, et un ticket déplacé à la main dans un autre outil ne
remonte pas. Le reste de la plateforme ne change pas d'un iota.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from choregos_contracts import InboundEvent, ProjectConfig
from choregos_core.domain import NewItem, TrackerStateMapping, WorkItemData


class InternalTracker:
    """La base de Choregos est la source ; ce connecteur ne parle à personne.

    `owns_items = False` est le fait important, et il a une histoire. Livré le 2026-09-23
    comme une suite de non-opérations, ce connecteur laissait deux trous que le banc a
    montrés le lendemain :

    - `create_item` rendait le TITRE comme clé de ticket. Les findings promus en tickets
      portaient donc des clés du genre `[docs] Base de profils candidats manquante`.
    - `list_candidates` rendant `[]`, la réconciliation ne découvrait jamais ces tickets :
      créés, ils restaient en `inbox` **pour toujours**.

    Les deux se soignent au même endroit, et pas ici : quand le tracker ne possède pas les
    tickets, c'est la plateforme qui leur donne une clé et qui les découvre dans sa propre
    base. Ce connecteur le DIT, l'orchestrateur en tient compte.
    """

    owns_items = False

    async def fetch_item(self, key: str) -> WorkItemData:
        # Le ticket vit dans la base de Choregos : l'orchestrateur l'a déjà en main, et ce
        # qu'on rendrait ici serait une copie plus pauvre. On rend donc le strict minimum.
        return WorkItemData(key=key, title=key)

    async def create_item(self, data: NewItem) -> str:
        # La clé est donnée par la plateforme (`owns_items = False`) : rendre le titre,
        # comme on le faisait, fabriquait des clés de ticket illisibles et non uniques.
        return ""

    async def set_state(self, key: str, state_mapping: TrackerStateMapping) -> None:
        return None

    async def upsert_status_comment(self, key: str, markdown: str, marker: str) -> None:
        return None

    async def comment(self, key: str, markdown: str) -> str:
        return ""

    async def link(self, a: str, b: str, relation: Literal["origin", "duplicate", "blocks"]) -> None:
        return None

    async def set_fields(self, key: str, fields: dict[str, Any]) -> None:
        return None

    async def list_candidates(self, project: ProjectConfig) -> list[str]:
        return []

    def parse_webhook(self, headers: Mapping[str, str], body: bytes) -> list[InboundEvent]:
        return []

    def verify_webhook(self, headers: Mapping[str, str], body: bytes) -> bool:
        # Aucun webhook n'est attendu : accepter serait ouvrir une porte qui ne mène nulle part.
        return False
