"""Validation statique du DSL de workflow (règles de docs/plan/01 §1.4).

Les erreurs sont **bloquantes** : un workflow invalide n'est jamais épinglé sur un ticket.
Les avertissements n'empêchent pas l'enregistrement mais remontent au front et à la CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import AgentActor, HumanActor, Workflow
from choregos_contracts.workflow import AGENT_WILDCARD

from ..errors import Issue
from ..gates import known_gates
from .yamlsource import json_pointer, locate

PROD_STATE_PREFIX = "deployed_prod"


@dataclass(slots=True)
class ValidationReport:
    valid: bool = True
    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    def error(self, code: str, message: str, path: list[str | int], source: Any = None) -> None:
        line, column = locate(source, path) if source is not None else (None, None)
        self.errors.append(Issue(code, message, json_pointer(path), line, column))
        self.valid = False

    def warn(self, code: str, message: str, path: list[str | int], source: Any = None) -> None:
        line, column = locate(source, path) if source is not None else (None, None)
        self.warnings.append(Issue(code, message, json_pointer(path), line, column))

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "errors": [i.to_dict() for i in self.errors],
            "warnings": [i.to_dict() for i in self.warnings],
        }


def validate_workflow(wf: Workflow, source: Any = None) -> ValidationReport:
    """Applique les règles statiques du DSL à un workflow déjà désérialisé."""
    report = ValidationReport()
    states = set(wf.states)
    gates = set(known_gates())

    _check_references(wf, states, gates, report, source)
    _check_initial_and_terminal(wf, report, source)
    _check_reachability(wf, report, source)
    _check_prod_states(wf, report, source)
    _check_retries(wf, report, source)
    _check_gates_ont_de_la_matiere(wf, report, source)
    _check_roles_connus(wf, report, source)
    _check_warnings(wf, report, source)
    return report


def _check_references(
    wf: Workflow, states: set[str], gates: set[str], report: ValidationReport, source: Any
) -> None:
    for index, t in enumerate(wf.transitions):
        path: list[str | int] = ["transitions", index]
        if t.from_ != AGENT_WILDCARD and t.from_ not in states:
            report.error("state.unknown", f"état source inconnu : {t.from_}", [*path, "from"], source)
        if t.to not in states:
            report.error("state.unknown", f"état cible inconnu : {t.to}", [*path, "to"], source)
        if t.by is not None and t.by not in wf.actors:
            report.error("actor.unknown", f"acteur inconnu : {t.by}", [*path, "by"], source)
        for g_index, g in enumerate(t.gates):
            if g.name not in gates:
                report.error(
                    "gate.unknown",
                    f"gate inconnue : {g.name} (connues : {', '.join(sorted(gates))})",
                    [*path, "gates", g_index],
                    source,
                )
        for field_name in ("on_fail", "on_changes_requested"):
            retry = getattr(t, field_name)
            if retry is None:
                continue
            for attr in ("to", "escalate_to"):
                target = getattr(retry, attr)
                if target not in states:
                    report.error(
                        "state.unknown",
                        f"état inconnu dans {field_name}.{attr} : {target}",
                        [*path, field_name, attr],
                        source,
                    )
        if t.on_reject is not None and t.on_reject not in states:
            report.error("state.unknown", f"état inconnu : {t.on_reject}", [*path, "on_reject"], source)
        if t.review and t.review.agents:
            for a_index, actor_id in enumerate(t.review.agents):
                if actor_id not in wf.actors:
                    report.error(
                        "actor.unknown",
                        f"relecteur inconnu : {actor_id}",
                        [*path, "review", "agents", a_index],
                        source,
                    )
        if t.by:
            actor = wf.actors.get(t.by)
            if isinstance(actor, AgentActor) and actor.model.startswith("profile:"):
                name = actor.model.removeprefix("profile:")
                if not name:
                    report.error(
                        "model.profile_empty", "profil de modèle vide", ["actors", t.by, "model"], source
                    )

    for actor_id in wf.actors:
        if isinstance(actor, HumanActor) and actor.escalate_to and actor.escalate_to not in wf.actors:
            report.error(
                "actor.unknown",
                f"escalade vers un acteur inconnu : {actor.escalate_to}",
                ["actors", actor_id, "escalate_to"],
                source,
            )

    defaults = wf.defaults
    if defaults and defaults.from_any_agent_state:
        for attr in ("on_question", "on_budget_exceeded", "on_timeout"):
            target = getattr(defaults.from_any_agent_state, attr)
            if target is not None and target not in states:
                report.error(
                    "state.unknown",
                    f"état inconnu dans defaults.from_any_agent_state.{attr} : {target}",
                    ["defaults", "from_any_agent_state", attr],
                    source,
                )
    if (
        defaults
        and defaults.needs_human
        and defaults.needs_human.on_abandon
        and defaults.needs_human.on_abandon not in states
    ):
        report.error(
            "state.unknown",
            f"état inconnu : {defaults.needs_human.on_abandon}",
            ["defaults", "needs_human", "on_abandon"],
            source,
        )


def _check_initial_and_terminal(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """L'état initial est le premier déclaré (§1.4) ; il doit pouvoir mener à un état terminal."""
    initial = wf.initial_state
    if wf.states[initial].terminal:
        report.error(
            "workflow.initial_state_terminal",
            f"l'état initial ({initial}, le premier déclaré) ne peut pas être terminal",
            ["states", initial],
            source,
        )
    if not wf.terminal_states():
        report.error("workflow.no_terminal_state", "aucun état terminal déclaré", ["states"], source)

    # Un état qui n'est atteignable que par un chemin d'échec est légitime (needs_human) ;
    # un état qui n'est atteint par aucune transition, hors état initial, est une faute de frappe.
    targets = set(_all_targets(wf))
    for name in wf.states:
        if name != initial and name not in targets:
            report.error(
                "state.no_inbound",
                f"aucune transition ne mène à l'état {name} (l'état initial est {initial})",
                ["states", name],
                source,
            )


def _all_targets(wf: Workflow) -> list[str]:
    """Tous les états cibles déclarés, y compris les retours, escalades et défauts."""
    targets: list[str] = []
    for t in wf.transitions:
        targets.append(t.to)
        if t.on_reject:
            targets.append(t.on_reject)
        for retry in (t.on_fail, t.on_changes_requested):
            if retry is not None:
                targets += [retry.to, retry.escalate_to]
    defaults = wf.defaults
    if defaults and defaults.from_any_agent_state:
        d = defaults.from_any_agent_state
        targets += [x for x in (d.on_question, d.on_budget_exceeded, d.on_timeout) if x]
    if defaults and defaults.needs_human and defaults.needs_human.on_abandon:
        targets.append(defaults.needs_human.on_abandon)
    return targets


def _reachable_states(wf: Workflow) -> set[str]:
    agent_states = wf.agent_driven_states()
    seen = {wf.initial_state}
    stack = [wf.initial_state]
    while stack:
        current = stack.pop()
        for t in wf.transitions:
            if t.from_ != current and not (t.from_ == AGENT_WILDCARD and current in agent_states):
                continue
            targets = [t.to, t.on_reject]
            for retry in (t.on_fail, t.on_changes_requested):
                if retry is not None:
                    targets += [retry.to, retry.escalate_to]
            if wf.defaults and wf.defaults.from_any_agent_state and current in agent_states:
                d = wf.defaults.from_any_agent_state
                targets += [d.on_question, d.on_budget_exceeded, d.on_timeout]
            for target in targets:
                if target and target in wf.states and target not in seen:
                    seen.add(target)
                    stack.append(target)
    if wf.defaults and wf.defaults.needs_human and wf.defaults.needs_human.on_abandon:
        target = wf.defaults.needs_human.on_abandon
        if target in wf.states:
            seen.add(target)
    return seen


def _check_reachability(wf: Workflow, report: ValidationReport, source: Any) -> None:
    reachable = _reachable_states(wf)
    for name in wf.states:
        if name not in reachable:
            report.error("state.orphan", f"état orphelin (inatteignable) : {name}", ["states", name], source)
    terminals = set(wf.terminal_states())
    if terminals and not (terminals & reachable):
        report.error(
            "workflow.terminal_unreachable",
            "aucun état terminal n'est atteignable depuis l'état initial",
            ["states"],
            source,
        )


def _check_prod_states(wf: Workflow, report: ValidationReport, source: Any) -> None:
    for index, t in enumerate(wf.transitions):
        if t.to.startswith(PROD_STATE_PREFIX) and t.via != "release_train":
            report.error(
                "prod.requires_train",
                f"l'état {t.to} n'est atteignable que par `via: release_train` (la prod est un verrou)",
                ["transitions", index, "to"],
                source,
            )


def _check_retries(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """Toute transition de retour porte un `max_attempts` : pas de boucle infinie."""
    order = list(wf.states)
    position = {name: i for i, name in enumerate(order)}
    for index, t in enumerate(wf.transitions):
        if t.from_ == AGENT_WILDCARD or t.via == "release_train":
            continue
        backwards = position.get(t.to, 0) < position.get(t.from_, 0)
        if backwards and t.on_fail is None and t.on_changes_requested is None:
            actor = wf.actors.get(t.by) if t.by else None
            if isinstance(actor, AgentActor):
                report.error(
                    "transition.unbounded_retry",
                    f"transition de retour {t.from_} → {t.to} sans `on_fail.max_attempts`",
                    ["transitions", index],
                    source,
                )


# Ce qu'une garantie a besoin de trouver sur la transition pour pouvoir se prononcer.
# `outputs_present` sans `outputs:` ne regarde rien ; `evidence_facts` sans `keys:` non plus.
GATES_A_MATIERE: dict[str, tuple[str, str]] = {
    "outputs_present": ("outputs", "`outputs:` sur la transition"),
    "evidence_facts": ("keys", "`keys:` en paramètre de la garantie"),
}


def _check_gates_ont_de_la_matiere(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """Une garantie demandée sans rien à vérifier est refusée à l'écriture.

    Sinon elle se découvre en vol, et de la pire manière : le tableau de bord affiche une
    garantie verte que personne n'a évaluée. Le moteur refuse aussi au moment de trancher,
    mais un workflow faux doit échouer quand on l'écrit, pas quand un ticket le traverse.
    """
    for index, t in enumerate(wf.transitions):
        for g_index, g in enumerate(t.gates):
            besoin = GATES_A_MATIERE.get(g.name)
            if besoin is None:
                continue
            champ, comment = besoin
            if g.params.get("allow_empty") or g.params.get(champ) or (champ == "outputs" and t.outputs):
                continue
            report.error(
                "gate.sans_matiere",
                f"garantie `{g.name}` sans {comment} : elle n'aurait rien à vérifier",
                ["transitions", index, "gates", g_index],
                source,
            )


def _check_roles_connus(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """Un rôle que le paquet ne connaît pas exige un playbook apporté par le déploiement.

    Ce n'est pas une erreur : c'est même le but — un métier nomme ses rôles. Mais le
    playbook se résout PAR LE NOM DU RÔLE, et son absence ne se découvrirait qu'au premier
    ticket, sur un `playbook inconnu` au milieu d'une étape. Un avertissement à l'écriture
    coûte moins cher.
    """
    try:
        from choregos_playbooks import playbook_path
    except Exception:  # le validateur doit tourner sans le paquet de playbooks
        return
    for actor_id, actor in wf.actors.items():
        if not isinstance(actor, AgentActor):
            continue
        role = str(actor.playbook or actor.role)
        try:
            # On tente la RÉSOLUTION, pas une comparaison à la liste du paquet : un rôle
            # métier a son playbook dans le déploiement (`CHOREGOS_PLAYBOOKS_DIR`), et
            # avertir alors qu'il est là serait un avertissement qu'on apprend à ignorer.
            playbook_path(role)
        except FileNotFoundError:
            report.warn(
                "role.playbook_introuvable",
                f"aucun playbook pour le rôle `{role}` : le déploiement doit fournir "
                f"`{role}.md` (CHOREGOS_PLAYBOOKS_DIR)",
                ["actors", actor_id, "role"],
                source,
            )


def _check_warnings(wf: Workflow, report: ValidationReport, source: Any) -> None:
    if not any(name == "needs_human" or state.kind == "wait" for name, state in wf.states.items()):
        report.warn(
            "workflow.no_human_state",
            "aucun état d'attente humaine : le workflow ne pourra jamais demander d'arbitrage",
            ["states"],
            source,
        )
    roles = {str(actor.role) for actor in wf.actors.values() if isinstance(actor, AgentActor)}
    used_roles: set[str] = set()
    for t in wf.transitions:
        actor = wf.actors.get(t.by) if t.by else None
        if isinstance(actor, AgentActor):
            used_roles.add(str(actor.role))
    has_verify = "verify" in (roles & used_roles)
    has_pr = any(name.startswith("pr_") for name in wf.states)
    if has_pr and not has_verify:
        report.warn(
            "workflow.no_verify_before_pr",
            "aucune étape `verify` avant l'ouverture de PR",
            ["transitions"],
            source,
        )
    for actor_id in wf.actors:
        used = any(t.by == actor_id for t in wf.transitions) or any(
            actor_id in (t.review.agents if t.review else []) for t in wf.transitions
        )
        used = used or any(t.train and t.train.approval == actor_id for t in wf.transitions)
        if not used:
            report.warn(
                "actor.unused",
                f"acteur déclaré mais jamais utilisé : {actor_id}",
                ["actors", actor_id],
                source,
            )
