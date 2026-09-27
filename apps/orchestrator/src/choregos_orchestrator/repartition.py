# SPDX-License-Identifier: Apache-2.0
"""Quelle file porte quelle activité (docs/plan/02 §2.1).

Ce module existe parce que **deux côtés** ont besoin de la même table : le worker, pour savoir
ce qu'il enregistre, et les workflows, pour savoir **où planifier**. Tant qu'elle n'a vécu que
dans `worker.py`, seul le premier la lisait — un workflow n'importe pas le module du worker, qui
tire le client Temporal, l'API et la base. La table décrivait donc une répartition que rien
n'appliquait : Temporal planifie sur la file du *workflow* quand l'appel ne dit pas laquelle, et
la file `orchestrator` enregistre tout. Les trois workers spécialisés ne recevaient rien.

Ce n'est pas qu'une inefficacité. Chez Diametral, l'accès à l'API Kubernetes est ouvert par une
politique Cilium visant `choregos/queue: executor` — la file qui, d'après cette table, crée les
Jobs. `start_run` tournant en réalité sur `orchestrator`, la création de Job partait d'un pod sans
droit de sortie et expirait en `ConnectTimeout` : un run bloqué en `queued`, un ticket en échec.
Une politique de moindre privilège écrite d'après la répartition déclarée ne peut être juste que
si cette répartition est **appliquée**.

Le module ne dépend que de la bibliothèque standard : il doit rester importable depuis le bac à
sable d'un workflow.
"""

from __future__ import annotations

from typing import Any

#: Répartition des activités par file : les appels externes lents sont isolés pour ne pas bloquer
#: la boucle de décision. Une activité absente de cette table reste sur la file du workflow qui
#: l'appelle (`orchestrator`), qui porte tout — c'est le défaut, pas un oubli.
ACTIVITES_PAR_FILE: dict[str, tuple[str, ...]] = {
    "executor": (
        "prepare_stage",
        "start_run",
        "await_run",
        "cancel_run",
        "collect_spend",
        "record_run_outcome",
    ),
    "tracker": (
        "mirror_state",
        "update_status_comment",
        "create_human_request",
        "close_human_request",
        "notify",
        "open_pull_request",
        "enqueue_merge",
        "ensure_branch",
        "collect_run_artifacts",
        "evaluate_gates",
        "check_scope_violations",
        "triage_finding",
        "reconcile_tracker",
    ),
    "memory": (
        "ingest_sources",
        "ingest_alert",
        "write_run_lesson",
        "accept_pending_facts",
        "memory_ab_report",
    ),
}

#: L'index inverse, construit une fois. Une activité ne peut être que dans une file : si deux
#: entrées se contredisaient, la table serait ambiguë et personne ne le verrait.
FILE_PAR_ACTIVITE: dict[str, str] = {}
for _file, _noms in ACTIVITES_PAR_FILE.items():
    for _nom in _noms:
        if _nom in FILE_PAR_ACTIVITE:  # pragma: no cover - garde de cohérence de la table
            raise RuntimeError(
                f"l'activité {_nom!r} est déclarée dans deux files : {FILE_PAR_ACTIVITE[_nom]!r} et {_file!r}"
            )
        FILE_PAR_ACTIVITE[_nom] = _file
del _file, _noms, _nom


def nom_de_l_activite(activite: Any) -> str | None:
    """Le nom SOUS LEQUEL Temporal connaît une activité, ou `None` si ce n'en est pas une.

    C'est le nom déclaré par `@activity.defn`, pas celui de la fonction Python : les deux peuvent
    différer, et c'est le premier qui voyage dans l'historique.
    """
    if isinstance(activite, str):
        return activite
    definition = getattr(activite, "__temporal_activity_definition", None)
    if definition is None:
        return None
    nom = getattr(definition, "name", None)
    return nom if isinstance(nom, str) else None


def file_de(activite: Any) -> str | None:
    """La file qui porte cette activité, ou `None` si la table n'en déclare aucune."""
    nom = nom_de_l_activite(activite)
    return FILE_PAR_ACTIVITE.get(nom) if nom is not None else None
