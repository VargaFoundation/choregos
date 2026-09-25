"""Détection basique d'injection de prompt dans ce que l'agent va lire.

Un ticket, un commentaire, un document ou un souvenir de la mémoire est un texte que
personne de la plateforme n'a écrit : il peut porter des instructions déguisées en
contenu. La détection ici est **lexicale et honnête** — des motifs connus, pas une
compréhension. Elle ne remplace ni le bac à sable ni la vérification du diff (ADR 0010) ;
elle fait ce qu'OpenHands fait : voir venir les cas grossiers, et le dire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: (motif, libellé). Anglais et français : c'est la langue des tickets ici.
MOTIFS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|earlier|all|your|the)\b"
            r"[^.\n]{0,30}\b(instructions?|prompts?|rules?|guidelines?)\b",
            re.I,
        ),
        "ignorer les instructions précédentes",
    ),
    (
        re.compile(
            r"\b(ignore|oublie|oubliez|néglige|négligez)\b[^.\n]{0,40}\b(instructions?|consignes?|règles?)\b"
            r"[^.\n]{0,30}\b(précédentes?|antérieures?|initiales?|système)\b",
            re.I,
        ),
        "ignorer les instructions précédentes",
    ),
    (
        re.compile(
            r"\byou are now\b|\btu es (maintenant|désormais)\b|\bvous êtes (maintenant|désormais)\b", re.I
        ),
        "réassignation de rôle",
    ),
    (
        re.compile(r"\b(new|updated|real|actual) (system )?(instructions?|prompt)\s*:", re.I),
        "nouvelles instructions",
    ),
    (
        re.compile(
            r"\b(reveal|print|show|repeat|output|dump)\b[^.\n]{0,30}"
            r"\b(system prompt|instructions|your prompt)\b",
            re.I,
        ),
        "exfiltration du prompt",
    ),
    (
        re.compile(
            r"\b(affiche|révèle|montre|répète)\b[^.\n]{0,30}\b(prompt|instructions) (système|initial)", re.I
        ),
        "exfiltration du prompt",
    ),
    (
        re.compile(
            r"\bdo not (tell|inform|mention)\b[^.\n]{0,20}\b(user|human|operator)\b"
            r"|\bne (dis|parle)\b[^.\n]{0,20}\b(utilisateur|humain)\b",
            re.I,
        ),
        "dissimulation à l'humain",
    ),
    (
        re.compile(
            r"\b(send|post|upload|exfiltrate|transmit|envoie|poste|transmets)\b[^.\n]{0,60}\bhttps?://", re.I
        ),
        "envoi vers une URL",
    ),
    (re.compile(r"\b(curl|wget)\b[^|\n]{0,80}\|\s*(ba)?sh\b", re.I), "exécution d'un script téléchargé"),
    (
        re.compile(r"</?\s*(system|assistant|instructions)\s*>|\[/?INST\]|<\|im_start\|>|<\|system\|>", re.I),
        "balises de prompt",
    ),
    (re.compile(r"\b(BEGIN|END) (SYSTEM|HIDDEN) (PROMPT|INSTRUCTIONS)\b", re.I), "balises de prompt"),
    (re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{200,}={0,2}(?![A-Za-z0-9+/])"), "charge encodée"),
)


@dataclass(frozen=True, slots=True)
class Suspicion:
    source: str
    motif: str
    extrait: str

    def to_dict(self) -> dict[str, str]:
        return {"source": self.source, "motif": self.motif, "extrait": self.extrait}


def suspicions(texte: str, *, source: str = "texte") -> list[Suspicion]:
    """Les motifs d'injection trouvés dans un texte, avec un extrait court pour relire."""
    if not texte:
        return []
    trouvees: list[Suspicion] = []
    for motif, libelle in MOTIFS:
        for correspondance in motif.finditer(texte):
            debut = max(0, correspondance.start() - 20)
            extrait = texte[debut : correspondance.end() + 20].replace("\n", " ")
            trouvees.append(Suspicion(source, libelle, extrait[:120]))
            break  # un motif suffit à nommer la suspicion ; le reste est du bruit
    return trouvees


def analyser(sources: dict[str, str]) -> list[Suspicion]:
    """Analyse plusieurs sources nommées (`ticket.body`, `memory.<id>`…), dans l'ordre."""
    resultat: list[Suspicion] = []
    for nom, texte in sources.items():
        resultat.extend(suspicions(texte, source=nom))
    return resultat
