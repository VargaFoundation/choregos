# SPDX-License-Identifier: Apache-2.0
"""La vue « processus » d'un workflow : chaque transition dite en clair (ADR 0031).

La carte (`graph.to_graph`) montre la FORME d'un workflow ; elle restait muette sur ce qui compte à
un métier : qui fait passer un ticket d'un état au suivant, à quelles conditions, et ce qui arrive
quand ça rate. Ici, chaque transition devient une phrase, et ses éléments restent structurés pour
la console. Les phrases sont en anglais, comme l'interface (S5-01).
"""

from __future__ import annotations

from typing import Any

from choregos_contracts import Workflow
from choregos_contracts.workflow import AGENT_WILDCARD, AgentActor, HumanActor, Transition

#: Ce que vérifie chaque garantie, en une phrase. Un test exige un résumé pour chaque garantie
#: connue : une garantie nouvelle sans résumé ferait parler la vue processus dans le vide.
RESUMES: dict[str, str] = {
    "action_succeeded": "its governed action succeeded",
    "ci_green": "the CI pipeline is green",
    "coverage_delta_min": "test coverage does not drop below the threshold",
    "diff_size_max": "the change stays under the size limit",
    "evidence_facts": "the facts the agent counted meet the declared minimums",
    "evidence_present": "the platform measured the evidence the stage owes",
    "external": "an external check reports success",
    "flag_present": "the change sits behind a feature flag",
    "no_secrets": "no secret leaks into the change",
    "outputs_present": "the declared outputs are present",
    "provenance_signed": "the build provenance is signed",
    "review_approved": "the review is approved",
    "scans_ok": "security scans find nothing blocking",
    "scope_respected": "the agent stayed within its allowed paths",
    "tool_called": "the required tool was actually called",
}


def _affiche(wf: Workflow, etat: str) -> str:
    if etat == AGENT_WILDCARD:
        return "any agent step"
    state = wf.states.get(etat)
    return state.display if state else etat


def _qui(wf: Workflow, t: Transition) -> tuple[str, str, str]:
    """(type d'acteur, nom, description)."""
    if t.via == "release_train":
        env = t.train.env if t.train else "the target environment"
        return "release_train", "release_train", f"the release train to {env}"
    nom = t.by or "system"
    acteur = wf.actors.get(nom)
    if isinstance(acteur, AgentActor):
        return "agent", nom, f"the agent `{nom}` (role {acteur.role}, model {acteur.model})"
    if isinstance(acteur, HumanActor):
        delai = f", within {acteur.sla_hours} h" if acteur.sla_hours else ""
        return "human", nom, f"a person of group `{acteur.group}`{delai}"
    return "system", nom, "the platform"


def _action(t: Transition) -> str | None:
    """L'action gouvernée que la transition propose, en clair : quoi, quand, sous quelle validation."""
    if t.action is None:
        return None
    texte = f"proposing the governed action `{t.action.kind}`"
    if t.action.not_before:
        texte += f" not before `{t.action.not_before}`"
    if t.action.approval is not None:
        roles = ", ".join(f"{a.min} {a.role}" for a in t.action.approval.approvers)
        texte += f", approved by {roles}"
    else:
        texte += ", approved by a person when one of its operations requires it"
    return texte


def to_process(wf: Workflow) -> list[dict[str, Any]]:
    """Une étape par transition, dans l'ordre du YAML : qui agit, de quel état vers lequel, sous
    quelles garanties, ce qui doit être produit, et ce qui arrive en cas d'échec ou de rejet."""
    etapes = []
    for t in wf.transitions:
        type_, nom, qui = _qui(wf, t)
        noms = [g.name for g in t.gates]
        if t.action is not None and "action_succeeded" not in noms:
            noms.insert(0, "action_succeeded")  # implicite : une transition à action l'attend toujours
        garanties = [{"name": nom, "summary": RESUMES.get(nom, nom)} for nom in noms]
        conditions = []
        if t.outputs:
            conditions.append("it produced " + ", ".join(f"`{o}`" for o in t.outputs))
        conditions += [g["summary"] for g in garanties]
        phrase = f"From «{_affiche(wf, t.from_)}», {qui} moves the item to «{_affiche(wf, t.to)}»"
        action = _action(t)
        if action:
            phrase += f", after {action},"
        if conditions:
            phrase += " once " + "; ".join(conditions)
        phrase += "."
        echec = None
        if t.on_fail is not None:
            reprise, escalade = _affiche(wf, t.on_fail.to), _affiche(wf, t.on_fail.escalate_to)
            echec = (
                f"On failure it retries up to {t.on_fail.max_attempts} time(s) from «{reprise}», "
                f"then goes to «{escalade}»."
            )
            phrase += " " + echec
        rejet = f"If rejected, back to «{_affiche(wf, t.on_reject)}»." if t.on_reject else None
        if rejet:
            phrase += " " + rejet
        if t.timeout_hours:
            phrase += f" It times out after {t.timeout_hours} h."
        etapes.append(
            {
                "id": t.key,
                "from": t.from_,
                "from_display": _affiche(wf, t.from_),
                "to": t.to,
                "to_display": _affiche(wf, t.to),
                "actor": nom,
                "actor_type": type_,
                "who": qui,
                "outputs": list(t.outputs),
                "gates": garanties,
                "on_fail": echec,
                "on_reject": rejet,
                "timeout_hours": t.timeout_hours,
                "action": action,
                "sentence": phrase,
            }
        )
    return etapes
