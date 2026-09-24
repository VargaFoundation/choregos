"""Un exécuteur annonce ses capacités, et ne peut pas en annoncer une qu'il n'a pas.

C'est la **couture** par laquelle un bac à sable plus capable entrera : Agent Substrate,
un runtime à instantané, ou autre chose. Elle est posée maintenant, alors qu'un seul
exécuteur remplit une seule capacité, pour que ce jour-là rien d'autre ne bouge —
l'orchestrateur demandera ce que le runtime sait faire au lieu de le supposer.

Une capacité annoncée et absente est pire qu'absente : l'appelant s'y fie.
"""

from __future__ import annotations

import inspect

import pytest
from choregos_adapters import available, build
from choregos_adapters.base import CAPACITES_EXECUTEUR

pytestmark = pytest.mark.conformance

#: La méthode que chaque capacité oblige à implémenter. `queue` n'en impose aucune : elle
#: se joue dans `start` et `status`, et son comportement est vérifié par les tests de
#: l'exécuteur concerné (plafond atteint, file dans l'ordre d'arrivée).
METHODE_ATTENDUE = {"renew": "renew", "suspend": "suspend", "resume": "resume", "snapshot": "snapshot"}

# Construire un exécuteur hors cluster : sans `token`, le client Kubernetes va lire le
# jeton du compte de service monté dans un pod, et ce fichier n'existe pas ici.
KUBE = {"kubernetes": {"base_url": "https://exemple.invalid", "token": "hors-cluster", "verify": False}}
CONFIGS = {
    "k8s_job": KUBE,
    "tekton": KUBE,
    "local_docker": {},
    "aca": {"subscription_id": "s", "resource_group": "g", "environment_id": "e"},
    "fake": {},
}


def _executeurs() -> list[str]:
    return sorted(available("runtime"))


def test_le_vocabulaire_reste_ferme() -> None:
    """Une capacité inventée au fil de l'eau ne veut rien dire pour l'appelant."""
    assert frozenset({"queue", "renew", "suspend", "resume", "snapshot"}) == CAPACITES_EXECUTEUR


@pytest.mark.parametrize("nom", _executeurs())
def test_chaque_executeur_declare_ses_capacites(nom: str) -> None:
    executeur = build("runtime", nom, CONFIGS.get(nom, {}))
    capacites = getattr(executeur, "capabilities", None)
    assert isinstance(capacites, frozenset), f"{nom} n'annonce rien : un appelant devrait deviner"
    inconnues = capacites - CAPACITES_EXECUTEUR
    assert not inconnues, f"{nom} annonce des capacités hors vocabulaire : {sorted(inconnues)}"


@pytest.mark.parametrize("nom", _executeurs())
def test_ce_qui_est_annonce_est_implemente(nom: str) -> None:
    executeur = build("runtime", nom, CONFIGS.get(nom, {}))
    for capacite in sorted(getattr(executeur, "capabilities", frozenset())):
        methode = METHODE_ATTENDUE.get(capacite)
        if methode is None:
            continue
        fonction = getattr(executeur, methode, None)
        assert callable(fonction), f"{nom} annonce `{capacite}` sans `{methode}()`"
        assert inspect.iscoroutinefunction(fonction), f"{nom}.{methode}() doit être asynchrone"


def test_aucun_executeur_ne_sait_encore_prendre_un_instantane() -> None:
    """Ce test dit un ÉTAT, pas une règle : le jour où l'un d'eux sait le faire, il
    échoue, et c'est le moment de relire l'ADR 0013 — un pool de runners tièdes devient
    alors défendable, et la reprise après perte d'un nœud cesse de tout rejouer."""
    avec_instantane = [
        nom
        for nom in _executeurs()
        if "snapshot" in getattr(build("runtime", nom, CONFIGS.get(nom, {})), "capabilities", frozenset())
    ]
    assert avec_instantane == [], (
        f"{avec_instantane} sait prendre un instantané : relire docs/adr/0013 "
        "(pool de runners tièdes, reprise sans rejeu)"
    )
