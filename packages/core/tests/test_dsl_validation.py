"""Validation du DSL : les trois templates passent, douze documents fautifs sont localisés."""

from __future__ import annotations

from pathlib import Path

import pytest
from choregos_core import ValidationError, parse_workflow
from choregos_core.dsl import TEMPLATE_NAMES, template_yaml

BASE = """
apiVersion: choregos/v1
kind: Workflow
metadata: {{ name: t, version: 1 }}
actors:
  dev: {{ type: agent, role: implement }}
  ci: {{ type: system }}
  owner: {{ type: human, group: po }}
states:
  inbox: {{ display: Inbox }}
  doing: {{ display: Doing }}
  done: {{ display: Done, terminal: true }}
transitions:
{transitions}
defaults:
  from_any_agent_state: {{ on_question: doing }}
"""

RETRY = "{to: inbox, max_attempts: 2, escalate_to: done}"
OK_TRANSITIONS = (
    "  - {id: t1, from: inbox, to: doing, by: dev, on_fail: " + RETRY + "}\n"
    "  - {id: t2, from: doing, to: done, by: ci}\n"
)


def wf_text(transitions: str = OK_TRANSITIONS, **kwargs: str) -> str:
    return BASE.format(transitions=transitions, **kwargs)


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_templates_are_valid(name: str) -> None:
    workflow, report = parse_workflow(template_yaml(name), strict=False)
    assert report.valid, [i.format() for i in report.errors]
    assert report.warnings == [], [i.format() for i in report.warnings]
    assert workflow.metadata.name == name


def test_base_fixture_is_valid() -> None:
    _, report = parse_workflow(wf_text(), strict=False)
    assert report.valid, [i.format() for i in report.errors]


# ───────────────────────── les douze cas invalides ─────────────────────────


def _errors(text: str) -> list[str]:
    try:
        _workflow, report = parse_workflow(text, strict=False)
    except ValidationError as exc:
        assert all(i.line is not None for i in exc.issues if i.code.startswith("schema.")), (
            "les erreurs de schéma doivent être localisées"
        )
        return [i.code for i in exc.issues]
    assert not report.valid, "ce document aurait dû être refusé"
    for issue in report.errors:
        assert issue.path, "toute erreur porte un chemin"
    return [i.code for i in report.errors]


def test_01_unknown_target_state() -> None:
    codes = _errors(wf_text("  - {id: t1, from: inbox, to: nulle_part, by: dev}\n"))
    assert "state.unknown" in codes


def test_02_unknown_source_state() -> None:
    codes = _errors(
        wf_text(
            "  - {id: t1, from: ailleurs, to: done, by: dev}\n  - {id: t2, from: inbox, to: doing, by: ci}\n"
        )
    )
    assert "state.unknown" in codes


def test_03_unknown_actor() -> None:
    codes = _errors(
        wf_text("  - {id: t1, from: inbox, to: doing, by: fantome}\n  - {from: doing, to: done, by: ci}\n")
    )
    assert "actor.unknown" in codes


def test_04_unknown_gate() -> None:
    codes = _errors(
        wf_text(
            "  - {id: t1, from: inbox, to: doing, by: dev, gates: [licorne_verte]}\n"
            "  - {from: doing, to: done, by: ci}\n"
        )
    )
    assert "gate.unknown" in codes


def test_05_no_terminal_state() -> None:
    text = wf_text().replace("done: { display: Done, terminal: true }", "done: { display: Done }")
    assert "workflow.no_terminal_state" in _errors(text)


def test_06_orphan_state() -> None:
    text = wf_text().replace(
        "  done: { display: Done, terminal: true }",
        "  done: { display: Done, terminal: true }\n  oublie: { display: Oublié }",
    )
    codes = _errors(text)
    assert "state.no_inbound" in codes or "state.orphan" in codes


def test_07_prod_state_without_train() -> None:
    text = wf_text(
        "  - {id: t1, from: inbox, to: doing, by: dev}\n"
        "  - {id: t2, from: doing, to: deployed_prod, by: ci}\n"
    ).replace(
        "  done: { display: Done, terminal: true }",
        "  done: { display: Done, terminal: true }\n  deployed_prod: { display: Prod, terminal: true }",
    )
    assert "prod.requires_train" in _errors(text)


def test_08_unbounded_agent_retry() -> None:
    text = wf_text(
        "  - {id: t1, from: inbox, to: doing, by: ci}\n"
        "  - {id: t2, from: doing, to: inbox, by: dev}\n"
        "  - {id: t3, from: doing, to: done, by: ci}\n"
    )
    assert "transition.unbounded_retry" in _errors(text)


def test_09_retry_targets_unknown_state() -> None:
    text = wf_text(
        "  - {id: t1, from: inbox, to: doing, by: dev, "
        "on_fail: {to: nulle_part, max_attempts: 2, escalate_to: done}}\n"
        "  - {from: doing, to: done, by: ci}\n"
    )
    assert "state.unknown" in _errors(text)


def test_10_transition_without_actor_or_train() -> None:
    codes = _errors(wf_text("  - {id: t1, from: inbox, to: doing}\n  - {from: doing, to: done, by: ci}\n"))
    assert any(code.startswith("schema.") for code in codes)


def test_11_initial_state_terminal() -> None:
    text = wf_text().replace("  inbox: { display: Inbox }", "  inbox: { display: Inbox, terminal: true }")
    assert "workflow.initial_state_terminal" in _errors(text)


def test_12_defaults_point_to_unknown_state() -> None:
    text = wf_text().replace("on_question: doing", "on_question: nulle_part")
    assert "state.unknown" in _errors(text)


def test_13_bad_yaml_is_localised() -> None:
    with pytest.raises(ValidationError) as exc:
        parse_workflow("metadata:\n  name: [oups\n")
    assert exc.value.issues[0].code == "yaml.syntax"
    assert exc.value.issues[0].line is not None


def test_14_unknown_field_is_refused() -> None:
    codes = _errors(
        wf_text().replace("metadata: { name: t, version: 1 }", "metadata: { name: t, version: 1, oups: 1 }")
    )
    assert any(code.startswith("schema.") for code in codes)


def test_warning_when_no_human_state() -> None:
    """Un workflow sans état d'attente ne pourra jamais demander d'arbitrage : avertissement."""
    _, report = parse_workflow(wf_text(), strict=False)
    assert report.valid
    assert any(i.code == "workflow.no_human_state" for i in report.warnings), [
        i.code for i in report.warnings
    ]


def test_no_warning_when_wait_state_exists() -> None:
    text = wf_text().replace("  inbox: { display: Inbox }", "  inbox: { display: Inbox, kind: wait }")
    _, report = parse_workflow(text, strict=False)
    assert not any(i.code == "workflow.no_human_state" for i in report.warnings)


def test_unused_actor_is_reported_as_warning() -> None:
    text = wf_text().replace(
        "  owner: { type: human, group: po }",
        "  owner: { type: human, group: po }\n  ghost: { type: human, group: none }",
    )
    _, report = parse_workflow(text, strict=False)
    codes = [i.code for i in report.warnings]
    assert "actor.unused" in codes


def test_error_message_carries_line_and_column() -> None:
    text = wf_text(
        "  - {id: t1, from: inbox, to: nulle_part, by: dev}\n  - {from: doing, to: done, by: ci}\n"
    )
    _, report = parse_workflow(text, strict=False)
    issue = next(i for i in report.errors if i.code == "state.unknown")
    assert issue.line and issue.line > 0
    assert "transitions[0].to" in (issue.path or "")
    assert "ligne" in issue.format()


def test_13_garantie_sans_matiere() -> None:
    """`outputs_present` sans `outputs:` ne regarde rien. Le moteur refuse aussi au moment
    de trancher, mais un workflow faux doit échouer quand on l'écrit : sinon il s'épingle
    sur un ticket et la garantie ne se découvre qu'en vol, verte et vide."""
    sans = (
        "  - {id: t1, from: inbox, to: doing, by: dev, gates: [outputs_present], on_fail: "
        + RETRY
        + "}\n  - {id: t2, from: doing, to: done, by: ci}\n"
    )
    assert "gate.sans_matiere" in _errors(wf_text(sans))

    avec = sans.replace("gates: [outputs_present]", "outputs: [profils], gates: [outputs_present]")
    _wf, rapport = parse_workflow(wf_text(avec), strict=False)
    assert rapport.valid, [i.format() for i in rapport.errors]

    assume = sans.replace(
        "gates: [outputs_present]",
        "gates: [{name: outputs_present, params: {allow_empty: true}}]",
    )
    _wf2, rapport2 = parse_workflow(wf_text(assume), strict=False)
    assert rapport2.valid, "on peut assumer l'absence de sorties, il faut l'écrire"


def test_14_evidence_facts_sans_keys() -> None:
    """Même règle pour la garantie des preuves métier : sans `keys:`, elle ne lit rien."""
    sans = (
        "  - {id: t1, from: inbox, to: doing, by: dev, gates: [evidence_facts], on_fail: "
        + RETRY
        + "}\n  - {id: t2, from: doing, to: done, by: ci}\n"
    )
    assert "gate.sans_matiere" in _errors(wf_text(sans))

    avec = sans.replace(
        "gates: [evidence_facts]",
        "gates: [{name: evidence_facts, params: {keys: [profils_retenus]}}]",
    )
    _wf, rapport = parse_workflow(wf_text(avec), strict=False)
    assert rapport.valid, [i.format() for i in rapport.errors]


def test_15_un_role_metier_est_un_nom_de_plein_droit() -> None:
    """Avant le 2026-09-24 il fallait écrire `role: custom` + `playbook: sourcing` : cela
    fonctionnait, mais le board affichait « custom » pour toutes les étapes d'un métier et
    les évals ne savaient pas de quoi il s'agissait (ADR 0012, limite n°4)."""
    metier = BASE.replace("dev: {{ type: agent, role: implement }}", "dev: {{ type: agent, role: sourcing }}")
    _wf, rapport = parse_workflow(metier.format(transitions=OK_TRANSITIONS), strict=False)
    assert rapport.valid, [i.format() for i in rapport.errors]

    # Sans playbook résolvable, un avertissement — pas une erreur : c'est au déploiement
    # de l'apporter, et le validateur n'a pas à savoir ce qu'il montera.
    codes = [i.code for i in rapport.warnings]
    assert "role.playbook_introuvable" in codes, codes


def test_16_un_role_du_paquet_n_avertit_pas(tmp_path: Path) -> None:
    """Et le playbook du déploiement fait taire l'avertissement : avertir alors qu'il est
    là serait un avertissement qu'on apprend à ignorer."""
    import os

    (tmp_path / "sourcing.md").write_text("Tu fais du sourcing.", encoding="utf-8")
    ancien = os.environ.get("CHOREGOS_PLAYBOOKS_DIR")
    os.environ["CHOREGOS_PLAYBOOKS_DIR"] = str(tmp_path)
    try:
        metier = BASE.replace(
            "dev: {{ type: agent, role: implement }}", "dev: {{ type: agent, role: sourcing }}"
        )
        _wf, rapport = parse_workflow(metier.format(transitions=OK_TRANSITIONS), strict=False)
    finally:
        if ancien is None:
            del os.environ["CHOREGOS_PLAYBOOKS_DIR"]
        else:
            os.environ["CHOREGOS_PLAYBOOKS_DIR"] = ancien
    # Le fixture de base porte d'autres avertissements (pas d'état d'attente humaine) :
    # ce qui compte ici est qu'il n'y en ait AUCUN sur le playbook.
    assert "role.playbook_introuvable" not in [i.code for i in rapport.warnings], [
        i.format() for i in rapport.warnings
    ]


def test_17_un_role_qui_n_est_pas_un_identifiant_est_refuse() -> None:
    """Un rôle sert de NOM DE FICHIER pour le playbook : une espace ou une barre oblique
    y ferait chercher ailleurs que prévu."""
    mauvais = BASE.replace(
        "dev: {{ type: agent, role: implement }}", 'dev: {{ type: agent, role: "../secrets" }}'
    )
    assert any(code.startswith("schema.") for code in _errors(mauvais.format(transitions=OK_TRANSITIONS)))
