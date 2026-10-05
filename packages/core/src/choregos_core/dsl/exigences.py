# SPDX-License-Identifier: Apache-2.0
"""Ce que les workflows d'un projet exigent de ses connecteurs (ADR 0034).

La revue produit du 2026-10-05 : les réglages d'un projet RH montraient `scm`, `ci`, `cd` — un
dépôt, une intégration continue et un train qu'il n'a pas. Ce qu'un projet doit brancher se
DÉDUIT de ce que ses workflows font :

- une garantie lit des capacités (`ci_green` la CI, `scope_respected` un diff) : `gate_needs` ;
- un agent dont le rôle travaille dans un dépôt (`implement`, `fix_ci`…) exige un `scm` ;
- un train de release, un état `deployed_prod*` ou une vérification en prod exigent un `cd` ;
- un état à effet `pr_*` ou `merged*` exige un `scm` (l'interpréteur ouvre ou fusionne une PR) ;
- des agents tournent quelque part (`runtime`) et appellent des modèles (`gateway`) ;
- les tickets vivent dans un `tracker` — l'interne, tant qu'on n'en branche pas un autre.

Chaque exigence dit POURQUOI : la console montre la raison, pas seulement la case.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from choregos_contracts import Workflow
from choregos_contracts.enums import ActorType

from ..gates import gate_needs

#: Les rôles dont le playbook travaille dans un dépôt : ils lisent un diff, poussent une branche.
ROLES_DANS_UN_DEPOT = frozenset({"implement", "fix_ci", "address_review", "review", "release_notes"})
#: Ceux qui relancent la CI en plus.
ROLES_SUR_LA_CI = frozenset({"fix_ci"})
#: Ceux qui regardent la production.
ROLES_EN_PRODUCTION = frozenset({"verify_prod"})

#: L'ordre dans lequel la console les montre.
ORDRE = ("tracker", "scm", "ci", "cd", "runtime", "gateway", "memory", "notify")


@dataclass(frozen=True)
class Exigence:
    capacite: str
    raisons: tuple[str, ...]


class _Raisons:
    """Les raisons de chaque capacité, dans l'ordre où elles se découvrent, sans doublon."""

    def __init__(self) -> None:
        self.par_capacite: dict[str, list[str]] = {
            "tracker": ["work items live in a tracker: the internal one unless you connect another"]
        }

    def exiger(self, capacite: str, raison: str) -> None:
        liste = self.par_capacite.setdefault(capacite, [])
        if raison not in liste:
            liste.append(raison)


def _acteurs(workflow: Workflow, raisons: _Raisons) -> None:
    nom = workflow.metadata.name
    for acteur, spec in workflow.actors.items():
        if spec.type != ActorType.AGENT:
            continue
        raisons.exiger("runtime", f"{nom}: the agent {acteur} runs somewhere")
        raisons.exiger("gateway", f"{nom}: the agent {acteur} calls models")
        role = str(getattr(spec, "role", ""))
        if role in ROLES_DANS_UN_DEPOT:
            raisons.exiger("scm", f"{nom}: the agent {acteur} ({role}) works in a repository")
        if role in ROLES_SUR_LA_CI:
            raisons.exiger("ci", f"{nom}: the agent {acteur} ({role}) reruns the pipeline")
        if role in ROLES_EN_PRODUCTION:
            raisons.exiger("cd", f"{nom}: the agent {acteur} ({role}) checks production")


def _etats_et_transitions(workflow: Workflow, raisons: _Raisons) -> None:
    nom = workflow.metadata.name
    for etat in workflow.states:
        if etat.startswith(("pr_", "merged")):
            raisons.exiger("scm", f"{nom}: the state {etat} opens or merges a pull request")
        if etat.startswith("deployed_prod"):
            raisons.exiger("cd", f"{nom}: the state {etat} deploys to production")
    for transition in workflow.transitions:
        ident = transition.id or f"{transition.from_}->{transition.to}"
        if transition.via == "release_train" or transition.train is not None:
            raisons.exiger("cd", f"{nom}: {ident} goes through a release train")
        for garantie in transition.gates:
            for capacite in sorted(gate_needs(garantie.name)):
                raisons.exiger(capacite, f"{nom}: the guarantee {garantie.name} on {ident}")


def exigences(workflows: Iterable[Workflow]) -> list[Exigence]:
    """Les capacités que ces workflows exigent, chacune avec ses raisons (sans doublon)."""
    raisons = _Raisons()
    for workflow in workflows:
        _acteurs(workflow, raisons)
        _etats_et_transitions(workflow, raisons)
    rang = {capacite: index for index, capacite in enumerate(ORDRE)}
    return [
        Exigence(capacite, tuple(liste))
        for capacite, liste in sorted(
            raisons.par_capacite.items(), key=lambda item: (rang.get(item[0], len(ORDRE)), item[0])
        )
    ]


__all__ = ["ORDRE", "ROLES_DANS_UN_DEPOT", "Exigence", "exigences"]
