# SPDX-License-Identifier: Apache-2.0
"""Ce qu'un workflow demande au train qu'il prend (ADR 0041).

La revue produit du 2026-10-07 l'a constaté : `train: {env: prod, approval: captain}` était écrit
dans les workflows livrés et lu par personne. L'orchestrateur ne passait au train que
l'environnement, et le train ne tirait son approbation QUE de la politique du projet — un projet
ne pouvait donc pas livrer un correctif sans humain et une fonctionnalité avec. Ces deux fonctions
disent, depuis le workflow ÉPINGLÉ du ticket, ce que son train exige.
"""

from __future__ import annotations

from typing import TypedDict

from choregos_contracts import HumanActor, SystemActor, Workflow
from choregos_contracts.workflow import Transition, effet_de_la_transition


class ApprobationDuTrain(TypedDict):
    """Ce qu'un ticket exige du départ de son lot — un dict : il voyage dans des signaux Temporal."""

    required: bool
    group: str | None


def prend_le_train(transition: Transition) -> bool:
    return transition.via == "release_train" or transition.train is not None


def env_du_train(transition: Transition) -> str:
    """L'environnement visé ; `prod` quand la transition ne le dit pas (`via: release_train` seul)."""
    return transition.train.env if transition.train else "prod"


def approbation_du_train(workflow: Workflow, transition: Transition) -> ApprobationDuTrain:
    """`train.approval` nomme un acteur humain : son groupe approuve le départ. Un acteur qui n'est
    pas humain exige quand même l'approbation — le groupe sera celui de la politique."""
    if transition.train is None or not transition.train.approval:
        return {"required": False, "group": None}
    acteur = workflow.actors.get(str(transition.train.approval))
    return {"required": True, "group": acteur.group if isinstance(acteur, HumanActor) else None}


def train_apres_fusion(workflow: Workflow) -> Transition | None:
    """Le train qu'un ticket prend juste après la fusion de sa PR, ou `None` s'il n'en prend pas :
    une étude qui fusionne un ADR ne va nulle part, une livraison prudente passe d'abord par staging.
    Le webhook de GitHub embarquait en PROD tout ticket fusionné, quel que soit son workflow."""
    for fusion in workflow.transitions:
        acteur = workflow.actors.get(fusion.by or "")
        if not isinstance(acteur, SystemActor) or effet_de_la_transition(fusion) != "merge_pr":
            continue
        for suite in workflow.transitions:
            if suite.from_ == fusion.to and prend_le_train(suite):
                return suite
    return None


__all__ = [
    "ApprobationDuTrain",
    "approbation_du_train",
    "env_du_train",
    "prend_le_train",
    "train_apres_fusion",
]
