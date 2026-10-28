# SPDX-License-Identifier: Apache-2.0
"""Le catalogue d'agents livré avec la plateforme (ADR 0040).

La revue du 2026-10-07 ne trouvait dans `/agents` que les deux coordinateurs du gabarit RH : les
workflows logiciels faisaient tourner des agents IMPLICITES (un rôle, un playbook), jamais
enregistrés — sans mesures, sans budget, sans versions. Le catalogue en fait des agents du registre,
prêts à installer : un par rôle du paquet, plus ceux qu'une étude demande, plus les clients externes
qui entrent par la porte MCP.

Une entrée est un fichier YAML : `catalogue` (sa version, le rôle qu'elle tient, une phrase) et
`agent` — le document qu'accepte `POST /orgs/{org}/agents`. Un agent interne n'y fixe ni modèle ni
runtime : il hérite de ceux du projet et du déploiement. Ses instructions sont précédées du contexte
du ticket (`_contexte.md`) au chargement : l'auteur d'une entrée écrit la tâche, pas la plomberie.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path
from typing import Any, Literal

import yaml

DOSSIER = Path(__file__).parent


@dataclass(frozen=True, slots=True)
class EntreeDuCatalogue:
    """Un agent que le catalogue propose ; `document` est un `AgentCreate`."""

    slug: str
    genre: Literal["internal", "external"]
    version: int
    role: str
    resume: str
    document: dict[str, Any]
    #: Pour un client externe : son identifiant côté page Integrations, et d'où il appelle.
    client: str | None = None
    portee: Literal["always", "cloud"] | None = None
    skills: tuple[str, ...] = field(default_factory=tuple)
    #: The output contract (S33-06): the names a transition that runs this agent declares. Empty for
    #: an entry that does not state it yet.
    outputs: tuple[str, ...] = field(default_factory=tuple)
    #: What it acts with: runner binaries, connectors, skills, knowledge — what an installer must provide.
    tools: tuple[str, ...] = field(default_factory=tuple)
    #: Its result informs a decision that people make: a workflow sends it to a human next.
    human_gate_after: bool = False
    #: Labels people find the agent by (S40-10); installed with it, added on upgrade, never removed.
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def empreinte(self) -> str:
        """Ce qui distingue deux versions de l'entrée : un changement de texte change l'empreinte."""
        brut = json.dumps(self.document, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return "sha256:" + hashlib.sha256(brut).hexdigest()


def _contexte() -> str:
    return (DOSSIER / "_contexte.md").read_text(encoding="utf-8")


def _lire(chemin: Path) -> EntreeDuCatalogue:
    return entry_of(yaml.safe_load(chemin.read_text(encoding="utf-8")))


def entry_of(brut: dict[str, Any]) -> EntreeDuCatalogue:
    """An entry from a catalogue document (`catalogue:` + `agent:`), wherever it was read: the core's
    directory or a plugin's agent file (ADR 0093). The context block precedes an internal agent's
    instructions either way."""
    meta, document = dict(brut["catalogue"]), dict(brut["agent"])
    genre = document.get("kind", "internal")
    spec = dict(document.get("spec") or {})
    if genre == "internal":
        spec["instructions"] = _contexte() + spec.get("instructions", "")
    document["spec"] = spec
    return EntreeDuCatalogue(
        slug=document["slug"],
        genre=genre,
        version=int(meta["version"]),
        role=str(meta["role"]),
        resume=str(meta["summary"]),
        document=document,
        client=meta.get("client"),
        portee=meta.get("reach"),
        skills=tuple(str(s["slug"]) for s in spec.get("skills") or []),
        outputs=tuple(str(o) for o in meta.get("outputs") or []),
        tools=tuple(str(t) for t in meta.get("tools") or []),
        human_gate_after=bool(meta.get("human_gate_after", False)),
        tags=tuple(str(t) for t in document.get("tags") or []),
    )


@cache
def entrees() -> tuple[EntreeDuCatalogue, ...]:
    """Les agents internes d'abord, puis les clients externes, chacun dans l'ordre des noms."""
    internes = sorted((DOSSIER / "agents").glob("*.yaml"))
    externes = sorted((DOSSIER / "clients").glob("*.yaml"))
    return tuple(_lire(chemin) for chemin in [*internes, *externes])


def entree(slug: str) -> EntreeDuCatalogue | None:
    return next((e for e in entrees() if e.slug == slug), None)


def skills_du_catalogue() -> tuple[str, ...]:
    return tuple(sorted(d.name for d in (DOSSIER / "skills").iterdir() if (d / "SKILL.md").is_file()))


def fichiers_de_la_skill(nom: str) -> dict[str, str]:
    """Les fichiers d'une skill du catalogue, chemin relatif → texte ; `KeyError` si elle n'existe pas."""
    dossier = DOSSIER / "skills" / nom
    if not (dossier / "SKILL.md").is_file():
        raise KeyError(nom)
    return {
        chemin.relative_to(dossier).as_posix(): chemin.read_text(encoding="utf-8")
        for chemin in sorted(dossier.rglob("*"))
        if chemin.is_file()
    }


__all__ = [
    "EntreeDuCatalogue",
    "entree",
    "entrees",
    "entry_of",
    "fichiers_de_la_skill",
    "skills_du_catalogue",
]
