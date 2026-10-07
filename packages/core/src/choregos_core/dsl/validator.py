# SPDX-License-Identifier: Apache-2.0
"""Validation statique du DSL de workflow (règles de docs/plan/01 §1.4).

Les erreurs sont **bloquantes** : un workflow invalide n'est jamais épinglé sur un ticket.
Les avertissements n'empêchent pas l'enregistrement mais remontent au front et à la CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import AgentActor, HumanActor, SystemActor, Workflow
from choregos_contracts.workflow import (
    AGENT_WILDCARD,
    PREFIXE_PRODUCTION,
    effet_de_la_transition,
    effet_implicite,
    est_un_etat_de_production,
)

from ..errors import Issue
from ..gates import known_gates
from .dates import champ_de
from .yamlsource import json_pointer, locate

#: Lu encore pour les workflows écrits avant #175 ; un état de production s'écrit `production: true`.
PROD_STATE_PREFIX = PREFIXE_PRODUCTION


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
    _check_inputs(wf, report, source)
    _check_actions(wf, report, source)
    _check_tasks(wf, report, source)
    _check_effets(wf, report, source)
    _check_warnings(wf, report, source)
    return report


def _check_inputs(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """Les champs d'un ticket se décrivent par un JSON Schema d'OBJET valide (ADR 0031)."""
    schema = wf.metadata.inputs
    if schema is None:
        return
    import jsonschema

    try:
        jsonschema.Draft202012Validator.check_schema(schema)
    except jsonschema.SchemaError as erreur:
        report.error(
            "inputs.invalid",
            f"`metadata.inputs` is not a valid JSON Schema: {erreur.message}",
            ["metadata", "inputs"],
            source,
        )
        return
    if schema.get("type", "object") != "object":
        report.error(
            "inputs.not_object",
            "`metadata.inputs` describes an object: the work item's fields",
            ["metadata", "inputs"],
            source,
        )


def _check_actions(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """Une action gouvernée (S20-05) se propose par une transition de la PLATEFORME, sa date se lit
    dans un champ que le workflow déclare comme une date, et `action_succeeded` n'a de sens qu'avec
    une action à juger."""
    champs = dict((wf.metadata.inputs or {}).get("properties") or {})
    for index, t in enumerate(wf.transitions):
        chemin: list[str | int] = ["transitions", index]
        if t.action is None:
            for g_index, g in enumerate(t.gates):
                if g.name == "action_succeeded":
                    report.error(
                        "gate.sans_matiere",
                        "guarantee `action_succeeded` without `action:`: there is no action to judge",
                        [*chemin, "gates", g_index],
                        source,
                    )
            continue
        if t.on_fail is None:
            report.error(
                "action.on_fail_missing",
                "an action can fail or be rejected: `on_fail: {to, max_attempts, escalate_to}` says where "
                "the work item goes — without it, it would propose the same action forever",
                [*chemin, "action"],
                source,
            )
        acteur = wf.actors.get(t.by) if t.by else None
        if not isinstance(acteur, SystemActor):
            report.error(
                "action.not_system",
                "only a platform transition (`by:` a `system` actor) proposes an action: "
                "an agent calls tools, a person decides",
                [*chemin, "action"],
                source,
            )
        if t.action.not_before is None:
            continue
        nom = champ_de(t.action.not_before)
        declare = champs.get(nom)
        if not isinstance(declare, dict):
            report.error(
                "action.date_field_unknown",
                f"`not_before` reads `fields.{nom}`, which `metadata.inputs` does not declare",
                [*chemin, "action", "not_before"],
                source,
            )
        elif declare.get("format") not in {"date", "date-time"}:
            report.error(
                "action.date_field_not_date",
                f"`fields.{nom}` is not declared as a date (`format: date` or `date-time`)",
                [*chemin, "action", "not_before"],
                source,
            )


def _check_tasks(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """Une tâche (S20-06) est faite par une PERSONNE ; son formulaire est un JSON Schema d'objet, et
    chacune de ses propriétés est un champ que le workflow déclare : les valeurs y sont versées, et
    une action suivante les lit."""
    champs = dict((wf.metadata.inputs or {}).get("properties") or {})
    for index, t in enumerate(wf.transitions):
        if t.task is None:
            continue
        chemin: list[str | int] = ["transitions", index, "task"]
        if not isinstance(wf.actors.get(t.by) if t.by else None, HumanActor):
            report.error(
                "task.not_human",
                "a task is done by a person: `by:` a `human` actor",
                chemin,
                source,
            )
        import jsonschema

        try:
            jsonschema.Draft202012Validator.check_schema(t.task.form)
        except jsonschema.SchemaError as erreur:
            report.error("task.form_invalid", f"the form is not a JSON Schema: {erreur.message}",
                         [*chemin, "form"], source)  # fmt: skip
            continue
        proprietes = t.task.form.get("properties") or {}
        if t.task.form.get("type", "object") != "object" or not proprietes:
            report.error(
                "task.form_invalid",
                "the form describes an object, with at least one field to fill",
                [*chemin, "form"],
                source,
            )
            continue
        for nom in proprietes:
            if nom not in champs:
                report.error(
                    "task.field_unknown",
                    f"the form fills `fields.{nom}`, which `metadata.inputs` does not declare",
                    [*chemin, "form", "properties", nom],
                    source,
                )


def _check_references(
    wf: Workflow, states: set[str], gates: set[str], report: ValidationReport, source: Any
) -> None:
    for index, t in enumerate(wf.transitions):
        _check_transition_references(wf, t, ["transitions", index], states, gates, report, source)
    for actor_id, actor in wf.actors.items():
        # Avant le 2026-09-25, cette boucle relisait la variable `actor` de la boucle des
        # transitions : l'escalade d'un acteur humain vers un inconnu n'était jamais vue.
        if isinstance(actor, HumanActor) and actor.escalate_to and actor.escalate_to not in wf.actors:
            report.error(
                "actor.unknown",
                f"escalation to an unknown actor: {actor.escalate_to}",
                ["actors", actor_id, "escalate_to"],
                source,
            )
    _check_defaults_references(wf, states, report, source)


def _check_transition_references(
    wf: Workflow,
    t: Any,
    path: list[str | int],
    states: set[str],
    gates: set[str],
    report: ValidationReport,
    source: Any,
) -> None:
    def etat(nom: str | None, chemin: list[str | int], libelle: str) -> None:
        if nom is not None and nom not in states:
            report.error("state.unknown", f"{libelle}: {nom}", chemin, source)

    if t.from_ != AGENT_WILDCARD:
        etat(t.from_, [*path, "from"], "unknown source state")
    etat(t.to, [*path, "to"], "unknown target state")
    etat(t.on_reject, [*path, "on_reject"], "unknown state")
    if t.by is not None and t.by not in wf.actors:
        report.error("actor.unknown", f"unknown actor: {t.by}", [*path, "by"], source)
    for g_index, g in enumerate(t.gates):
        if g.name not in gates:
            report.error(
                "gate.unknown",
                f"unknown gate: {g.name} (known: {', '.join(sorted(gates))})",
                [*path, "gates", g_index],
                source,
            )
    for field_name in ("on_fail", "on_changes_requested"):
        retry = getattr(t, field_name)
        if retry is None:
            continue
        for attr in ("to", "escalate_to"):
            etat(getattr(retry, attr), [*path, field_name, attr], f"unknown state in {field_name}.{attr}")
    for a_index, actor_id in enumerate(t.review.agents if t.review and t.review.agents else []):
        if actor_id not in wf.actors:
            report.error(
                "actor.unknown",
                f"unknown reviewer: {actor_id}",
                [*path, "review", "agents", a_index],
                source,
            )
    actor = wf.actors.get(t.by) if t.by else None
    if isinstance(actor, AgentActor) and actor.model == "profile:":
        report.error("model.profile_empty", "empty model profile", ["actors", t.by, "model"], source)


def _check_defaults_references(wf: Workflow, states: set[str], report: ValidationReport, source: Any) -> None:
    defaults = wf.defaults
    if not defaults:
        return
    if defaults.from_any_agent_state:
        for attr in ("on_question", "on_budget_exceeded", "on_timeout"):
            target = getattr(defaults.from_any_agent_state, attr)
            if target is not None and target not in states:
                report.error(
                    "state.unknown",
                    f"unknown state in defaults.from_any_agent_state.{attr}: {target}",
                    ["defaults", "from_any_agent_state", attr],
                    source,
                )
    abandon = defaults.needs_human.on_abandon if defaults.needs_human else None
    if abandon and abandon not in states:
        report.error(
            "state.unknown", f"unknown state: {abandon}", ["defaults", "needs_human", "on_abandon"], source
        )


def _check_initial_and_terminal(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """L'état initial est celui que le document déclare (§1.4) ; il mène à un état terminal."""
    initial = wf.initial_state
    if initial not in wf.states:
        report.error(
            "workflow.initial_state_unknown",
            f"`initial: {initial}` names no declared state",
            ["initial"],
            source,
        )
        return
    if wf.states[initial].terminal:
        report.error(
            "workflow.initial_state_terminal",
            f"the initial state ({initial}) cannot be terminal: the work item would be born finished",
            ["states", initial],
            source,
        )
    if not wf.terminal_states():
        report.error("workflow.no_terminal_state", "no terminal state declared", ["states"], source)

    # Un état qui n'est atteignable que par un chemin d'échec est légitime (needs_human) ;
    # un état qui n'est atteint par aucune transition, hors état initial, est une faute de frappe.
    targets = set(_all_targets(wf))
    for name in wf.states:
        if name != initial and name not in targets:
            report.error(
                "state.no_inbound",
                f"no transition leads to the state {name} (the initial state is {initial})",
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
            report.error("state.orphan", f"orphan state (unreachable): {name}", ["states", name], source)
    terminals = set(wf.terminal_states())
    if terminals and not (terminals & reachable):
        report.error(
            "workflow.terminal_unreachable",
            "no terminal state is reachable from the initial state",
            ["states"],
            source,
        )


def _check_effets(wf: Workflow, report: ValidationReport, source: Any) -> None:
    """Ce que la plateforme fait s'ÉCRIT (#175) : `does` sur une transition système, `production`
    sur un état. Un effet encore déduit d'un nom se lit — un workflow publié ne change pas de
    comportement —, mais il s'annonce : un renommage le perdrait."""
    gestes = {"open_pr": "opens the PR", "merge_pr": "merges the PR"}
    for index, t in enumerate(wf.transitions):
        systeme = isinstance(wf.actors.get(t.by or ""), SystemActor)
        if t.does is not None and not systeme:
            message = f"`does: {t.does}` is a platform gesture: only a `system` actor carries it"
            report.error("transition.does_requires_system", message, ["transitions", index, "does"], source)
        implicite = effet_implicite(t.to)
        if systeme and t.does is None and implicite is not None:
            message = (
                f"the transition to `{t.to}` {gestes[implicite]} because of the state's NAME: "
                f"write `does: {implicite}`, so that a rename no longer changes it"
            )
            report.warn("workflow.effet_implicite", message, ["transitions", index, "to"], source)
    for nom, etat in wf.states.items():
        if nom.startswith(PREFIXE_PRODUCTION) and not etat.production:
            message = (
                f"the state `{nom}` is production because of its NAME: write `production: true`, "
                "so that a rename no longer lifts the lock"
            )
            report.warn("workflow.effet_implicite", message, ["states", nom], source)


def _check_prod_states(wf: Workflow, report: ValidationReport, source: Any) -> None:
    for index, t in enumerate(wf.transitions):
        if est_un_etat_de_production(t.to, wf.states.get(t.to)) and t.via != "release_train":
            report.error(
                "prod.requires_train",
                f"the state {t.to} can only be reached `via: release_train` (production is locked)",
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
                    f"return transition {t.from_} → {t.to} without `on_fail.max_attempts`",
                    ["transitions", index],
                    source,
                )


# Ce qu'une garantie a besoin de trouver sur la transition pour pouvoir se prononcer.
# `outputs_present` sans `outputs:` ne regarde rien ; `evidence_facts` sans `keys:` non plus.
GATES_A_MATIERE: dict[str, tuple[str, str]] = {
    "outputs_present": ("outputs", "`outputs:` on the transition"),
    "evidence_facts": ("keys", "`keys:` as a parameter of the guarantee"),
    "tool_called": ("tools", "`tools:` as a parameter of the guarantee"),
    "outputs_in": ("values", "`values:` as a parameter of the guarantee"),
    "markdown_sections": ("paths", "`paths:` as a parameter of the guarantee"),
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
                f"guarantee `{g.name}` without {comment}: it would have nothing to check",
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
        if actor.agent:
            # Un agent du registre apporte ses instructions (ADR 0033) : le playbook n'est plus
            # qu'un repli, et l'avertir serait un avertissement qu'on apprend à ignorer.
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
                f"no playbook for the role `{role}`: the deployment must provide "
                f"`{role}.md` (CHOREGOS_PLAYBOOKS_DIR)",
                ["actors", actor_id, "role"],
                source,
            )


def _check_warnings(wf: Workflow, report: ValidationReport, source: Any) -> None:
    if not any(name == "needs_human" or state.kind == "wait" for name, state in wf.states.items()):
        report.warn(
            "workflow.no_human_state",
            "no human waiting state: the workflow can never ask a person to arbitrate",
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
    has_pr = any(
        isinstance(wf.actors.get(t.by or ""), SystemActor) and effet_de_la_transition(t) == "open_pr"
        for t in wf.transitions
    )
    if has_pr and not has_verify:
        report.warn(
            "workflow.no_verify_before_pr",
            "no `verify` step before the pull request is opened",
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
                f"actor declared but never used: {actor_id}",
                ["actors", actor_id],
                source,
            )
