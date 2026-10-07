"""Les éditions typées d'un workflow, greffées dans le texte (ADR 0031, S16-11).

La propriété qui compte : une opération suivie de son inverse redonne les octets d'origine — sur
un document qui mélange les styles (flow, bloc, guillemets, commentaires partout, sans fin de ligne
finale) comme sur les gabarits livrés. Et une opération ne touche pas à ce qu'elle ne vise pas :
les commentaires restent.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_core import parse_workflow
from choregos_core.dsl import TEMPLATE_NAMES, template_yaml
from choregos_core.dsl.edition import EditionRefusee, editer

MELANGE = """apiVersion: choregos/v1
kind: Workflow
metadata: {name: arrivee, version: 3}  # la version courante
# Qui agit
actors:
  rh: {type: human, group: rh, sla_hours: 24}
  # le coordinateur
  coordinateur:
    type: agent
    role: refine
  plateforme: {type: system}
  ancien: {type: human, group: archives}
states:
  # la demande arrive ici
  demande: {display: Demande, kind: wait}
  "preparation":
    display: "Préparation"   # en cours
    kind: work
  attente_badge: {display: 'Badge à poser', kind: wait}
  orpheline: {display: Inutilisée}
  fait:
    display: Fait
    terminal: true
transitions:
  - {id: t-preparer, from: demande, to: preparation, by: coordinateur, gates: [outputs_present]}
  # la RH valide
  - id: t-valider
    from: "preparation"
    to: attente_badge
    by: rh
    timeout_hours: 48
    gates:
      - review_approved
      - {name: evidence_present, params: {kind: photo}}
  - {id: t-badge, from: attente_badge, to: fait, by: plateforme}
defaults:
  from_any_agent_state: {on_question: demande, on_timeout: demande}"""

#: Les transitions en dernier, sans fin de ligne : retirer ou ajouter la dernière touche la fin du fichier.
TRANSITIONS_EN_DERNIER = MELANGE[: MELANGE.index("defaults:")].rstrip("\n")

DOCUMENTS = {
    "melange": MELANGE,
    "melange-fin-de-ligne": MELANGE + "\n",
    "transitions-en-dernier": TRANSITIONS_EN_DERNIER,
    **{f"gabarit-{nom}": template_yaml(nom) for nom in TEMPLATE_NAMES},
}


def _commentaires(texte: str) -> list[str]:
    return [m.group(0) for m in re.finditer(r"#[^\n]*", texte)]


def _operations(texte: str) -> list[dict[str, Any]]:
    """Toutes les opérations qu'on peut tenter sur un document."""
    document = yaml.safe_load(texte)
    etats = list(document["states"])
    acteurs = list(document["actors"])
    transitions = [t for t in document["transitions"] if t.get("id")]
    ops: list[dict[str, Any]] = []
    for place in (None, 0, 1):
        ops.append({"op": "add_state", "name": "nouvel_etat", "spec": {"kind": "wait"}, "index": place})
        ops.append({"op": "add_actor", "name": "nouvel_acteur", "spec": {"type": "system"}, "index": place})
        ops.append(
            {
                "op": "add_transition",
                "transition": {"from": etats[0], "to": etats[-1], "by": acteurs[0]},
                "index": place,
            }
        )
    for etat in etats:
        ops += [
            {"op": "remove_state", "name": etat},
            {"op": "rename_state", "from": etat, "to": "renomme"},
            {"op": "set_state", "name": etat, "field": "display", "value": "Libellé neuf"},
            {"op": "set_state", "name": etat, "field": "tracker", "value": {"status": "En cours"}},
            {"op": "set_state", "name": etat, "field": "kind", "unset": True},
        ]
    for acteur in acteurs:
        ops += [
            {"op": "remove_actor", "name": acteur},
            {"op": "set_actor", "name": acteur, "field": "sla_hours", "value": 12},
        ]
    for transition in transitions:
        ident = transition["id"]
        ops += [
            {"op": "remove_transition", "id": ident},
            {"op": "set_transition", "id": ident, "field": "timeout_hours", "value": 72},
            {"op": "set_transition", "id": ident, "field": "by", "value": acteurs[-1]},
            {"op": "set_transition", "id": ident, "field": "to", "unset": True},
            {"op": "add_gate", "transition": ident, "gate": "scope_respected"},
            {
                "op": "add_gate",
                "transition": ident,
                "gate": {"name": "diff_size_max", "params": {"files": 9}},
                "index": 0,
            },
        ]
        for garantie in transition.get("gates") or []:
            nom = garantie if isinstance(garantie, str) else garantie["name"]
            ops.append({"op": "remove_gate", "transition": ident, "name": nom})
    return ops


def _cas() -> list[Any]:
    cas = []
    for nom, texte in DOCUMENTS.items():
        for i, op in enumerate(_operations(texte)):
            cas.append(pytest.param(texte, op, id=f"{nom}-{op['op']}-{i}"))
    return cas


@pytest.mark.parametrize(("texte", "op"), _cas())
def test_une_operation_puis_son_inverse_redonnent_les_octets(texte: str, op: dict[str, Any]) -> None:
    try:
        edition = editer(texte, [op])
    except EditionRefusee:
        return  # refusée : rien n'a changé, c'est un autre test qui dit quand refuser
    yaml.safe_load(edition.yaml)  # le texte édité est du YAML
    inverse = [o.model_dump(by_alias=True, exclude_none=True) for o in edition.inverse]
    assert editer(edition.yaml, inverse).yaml == texte
    if not op["op"].startswith("remove") and not op.get("unset"):
        assert _commentaires(texte) == [c for c in _commentaires(edition.yaml) if c in _commentaires(texte)]


def test_chaque_sorte_d_operation_s_applique_quelque_part() -> None:
    """La propriété ci-dessus ne vaut rien si tout est refusé : chaque sorte doit passer une fois."""
    vues: set[str] = set()
    for texte in DOCUMENTS.values():
        for op in _operations(texte):
            try:
                editer(texte, [op])
            except EditionRefusee:
                continue
            vues.add(op["op"] + (":unset" if op.get("unset") else ""))
    assert vues >= {
        "add_state",
        "remove_state",
        "rename_state",
        "set_state",
        "set_state:unset",
        "add_transition",
        "remove_transition",
        "set_transition",
        "set_transition:unset",
        "add_gate",
        "remove_gate",
        "add_actor",
        "remove_actor",
        "set_actor",
    }, vues


def _erreurs(texte: str) -> set[tuple[str, str | None]]:
    """Les erreurs du validateur, par sorte et par chemin : une édition n'en ajoute aucune."""
    return {(i.code, i.path) for i in parse_workflow(texte, strict=False)[1].errors}


def test_renommer_un_etat_suit_chaque_reference() -> None:
    edition = editer(MELANGE, [{"op": "rename_state", "from": "demande", "to": "accueil"}])
    assert _erreurs(edition.yaml) == _erreurs(MELANGE)
    workflow, _ = parse_workflow(edition.yaml, strict=False)
    assert "demande" not in workflow.states and "accueil" in workflow.states
    assert workflow.transitions[0].from_ == "accueil"
    assert workflow.defaults is not None and workflow.defaults.from_any_agent_state is not None
    assert workflow.defaults.from_any_agent_state.on_question == "accueil"
    # Un nom écrit entre guillemets le reste.
    renomme = editer(MELANGE, [{"op": "rename_state", "from": "preparation", "to": "instruction"}]).yaml
    assert '"instruction":' in renomme and 'from: "instruction"' in renomme


def test_une_garantie_ajoutee_garde_le_style_de_sa_liste() -> None:
    flow = editer(MELANGE, [{"op": "add_gate", "transition": "t-preparer", "gate": "scope_respected"}]).yaml
    assert "gates: [outputs_present, scope_respected]" in flow
    bloc = editer(MELANGE, [{"op": "add_gate", "transition": "t-valider", "gate": "scope_respected"}]).yaml
    assert "      - {name: evidence_present, params: {kind: photo}}\n      - scope_respected\n" in bloc
    sans = editer(MELANGE, [{"op": "add_gate", "transition": "t-badge", "gate": "ci_green"}]).yaml
    assert "{id: t-badge, from: attente_badge, to: fait, by: plateforme, gates: [ci_green]}" in sans
    for texte in (flow, bloc, sans):
        assert _erreurs(texte) == _erreurs(MELANGE)


@pytest.mark.parametrize(
    ("op", "motif"),
    [
        pytest.param({"op": "remove_state", "name": "demande"}, "still named", id="etat-reference"),
        pytest.param({"op": "remove_actor", "name": "rh"}, "still carries", id="acteur-reference"),
        pytest.param(
            {"op": "rename_state", "from": "demande", "to": "fait"}, "already", id="renommer-vers-un-existant"
        ),
        pytest.param(
            {"op": "rename_state", "from": "demande", "to": "Pas Un Nom"}, "state name", id="nom-invalide"
        ),
        pytest.param({"op": "add_state", "name": "demande"}, "already exists", id="etat-existant"),
        pytest.param(
            {"op": "remove_transition", "id": "t-inconnue"}, "does not exist", id="transition-inconnue"
        ),
        pytest.param(
            {"op": "set_transition", "id": "t-valider", "field": "gates", "value": []},
            "block style",
            id="valeur-en-bloc",
        ),
        pytest.param({"op": "set_transition", "id": "t-valider", "field": "id", "value": "x"}, "id", id="id"),
        pytest.param(
            {"op": "add_gate", "transition": "t-valider", "gate": "review_approved"},
            "already",
            id="garantie-en-double",
        ),
        # `on_timeout` nomme `plus_tard`, qui n'est pas (encore) un état : le renommage inverse le
        # renommerait aussi, et ne redonnerait pas les octets d'origine.
        pytest.param(
            {"op": "rename_state", "from": "attente_badge", "to": "plus_tard"},
            "already named",
            id="renommer-vers-un-nom-deja-cite",
        ),
        pytest.param(
            {
                "op": "add_transition",
                "transition": {"id": "t-preparer", "from": "demande", "to": "fait", "by": "rh"},
            },
            "already exists",
            id="transition-existante",
        ),
    ],
)
def test_ce_qui_est_refuse(op: dict[str, Any], motif: str) -> None:
    texte = MELANGE.replace("on_timeout: demande}", "on_timeout: plus_tard}")
    with pytest.raises(EditionRefusee, match=motif):
        editer(texte, [op])


def test_le_premier_champ_d_une_transition_en_bloc_ne_bouge_pas() -> None:
    """Il partage la ligne du tiret : le retirer ou insérer devant casserait la séquence."""
    texte = MELANGE.replace(
        '  - id: t-valider\n    from: "preparation"', '  - from: "preparation"\n    id: t-valider'
    )
    assert '  - from: "preparation"\n    id: t-valider' in texte
    with pytest.raises(EditionRefusee, match="dash's line"):
        editer(texte, [{"op": "set_transition", "id": "t-valider", "field": "from", "unset": True}])
    # Le reste de la transition se modifie.
    edition = editer(
        texte, [{"op": "set_transition", "id": "t-valider", "field": "timeout_hours", "unset": True}]
    )
    inverse = [o.model_dump(by_alias=True, exclude_none=True) for o in edition.inverse]
    assert editer(edition.yaml, inverse).yaml == texte


#: Un workflow écrit AVANT #175 : ses effets sont dans les noms (`pr_open`, `merged`, `deployed_prod`).
ANCIEN = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: ancien, version: 1 }
actors:
  ci: { type: system }
  captain: { type: human, group: release-captains, sla_hours: 4 }
  owner: { type: human, group: product-owners, sla_hours: 24 }
states:
  inbox: { display: À trier, kind: wait }
  in_progress: { display: En cours }
  pr_open: { display: PR ouverte, kind: wait }
  merged: { display: Fusionné }
  deployed_prod: { display: En production, terminal: true }
transitions:
  - { id: t-start, from: inbox, to: in_progress, by: owner }
  - { id: t-pr, from: in_progress, to: pr_open, by: ci }
  - { id: t-merge, from: pr_open, to: merged, by: ci }
  - id: t-deploy
    from: merged
    to: deployed_prod
    via: release_train
    train: { env: prod, approval: captain }
"""


def test_renommer_un_etat_a_effet_ecrit_d_abord_son_effet() -> None:
    """#175 : `pr_open` ouvrait la PR par son NOM. Renommé depuis la console, il l'aurait perdu sans
    le dire. Le renommage écrit d'abord `does: open_pr` : le workflow fait la même chose après."""
    from choregos_contracts.workflow import effet_de_la_transition

    edition = editer(ANCIEN, [{"op": "rename_state", "from": "pr_open", "to": "revue"}])
    apres, _ = parse_workflow(edition.yaml, strict=False)
    (t_pr,) = [t for t in apres.transitions if t.id == "t-pr"]
    assert (t_pr.to, t_pr.does, effet_de_la_transition(t_pr)) == ("revue", "open_pr", "open_pr")
    assert any("is now written" in a for a in edition.avertissements)
    assert editer(edition.yaml, edition.inverse).yaml == ANCIEN, "l'inverse défait le tout, à l'octet près"


def test_renommer_la_production_ne_leve_pas_le_verrou() -> None:
    edition = editer(ANCIEN, [{"op": "rename_state", "from": "deployed_prod", "to": "en_service"}])
    apres, rapport = parse_workflow(edition.yaml, strict=False)
    assert apres.states["en_service"].production is True
    assert rapport.valid, [e.message for e in rapport.errors]
    # Le verrou tient : une transition qui y mène sans train est refusée, quel que soit le nom.
    sans_train = edition.yaml.replace(
        "    via: release_train\n    train: { env: prod, approval: captain }\n", "    by: captain\n"
    )
    _, refus = parse_workflow(sans_train, strict=False)
    assert any(e.code == "prod.requires_train" for e in refus.errors)


def test_un_effet_ecrit_se_lit_et_un_effet_deduit_s_annonce() -> None:
    _, ancien = parse_workflow(ANCIEN, strict=False)
    assert len([w for w in ancien.warnings if w.code == "workflow.effet_implicite"]) == 3
    nouveau = editer(
        ANCIEN,
        [
            {"op": "set_transition", "id": "t-pr", "field": "does", "value": "open_pr"},
            {"op": "set_transition", "id": "t-merge", "field": "does", "value": "merge_pr"},
            {"op": "set_state", "name": "deployed_prod", "field": "production", "value": True},
        ],
    ).yaml
    _, ecrit = parse_workflow(nouveau, strict=False)
    assert [w for w in ecrit.warnings if w.code == "workflow.effet_implicite"] == []


def test_does_n_est_qu_un_geste_de_la_plateforme() -> None:
    humain = editer(ANCIEN, [{"op": "set_transition", "id": "t-start", "field": "does", "value": "open_pr"}])
    _, rapport = parse_workflow(humain.yaml, strict=False)
    assert any(e.code == "transition.does_requires_system" for e in rapport.errors)


def test_un_effet_sans_id_de_transition_ne_se_renomme_pas_en_silence() -> None:
    sans_id = ANCIEN.replace(
        "  - { id: t-pr, from: in_progress, to: pr_open, by: ci }",
        "  - { from: in_progress, to: pr_open, by: ci }",
    )
    with pytest.raises(EditionRefusee, match="has no `id`"):
        editer(sans_id, [{"op": "rename_state", "from": "pr_open", "to": "revue"}])


def test_un_effet_ecrit_garde_ce_que_le_validateur_en_deduit() -> None:
    """Après le renommage, la PR s'ouvre encore : le validateur le sait (`no_verify_before_pr`)."""
    _, avant = parse_workflow(ANCIEN, strict=False)
    renomme = editer(ANCIEN, [{"op": "rename_state", "from": "pr_open", "to": "revue"}]).yaml
    _, apres = parse_workflow(renomme, strict=False)
    for rapport in (avant, apres):
        assert any(w.code == "workflow.no_verify_before_pr" for w in rapport.warnings)


def test_renommer_vers_un_nom_a_effet_l_annonce() -> None:
    """Un nom en `pr_`, `merged` ou `deployed_prod` se lit encore comme un effet : le donner le dit."""
    edition = editer(ANCIEN, [{"op": "rename_state", "from": "in_progress", "to": "pr_brouillon"}])
    assert any("carries an effect through its name" in a for a in edition.avertissements)


def test_plusieurs_operations_s_annulent_dans_l_ordre_contraire() -> None:
    operations = [
        {"op": "add_state", "name": "verification", "spec": {"kind": "work"}},
        {
            "op": "add_transition",
            "transition": {"id": "t-verifier", "from": "attente_badge", "to": "verification", "by": "rh"},
        },
        {"op": "rename_state", "from": "verification", "to": "controle"},
        {"op": "add_gate", "transition": "t-verifier", "gate": "evidence_present"},
        {"op": "add_gate", "transition": "t-preparer", "gate": "no_secrets", "index": 0},
    ]
    edition = editer(MELANGE, operations)
    assert "controle" in yaml.safe_load(edition.yaml)["states"]
    inverse = [o.model_dump(by_alias=True, exclude_none=True) for o in edition.inverse]
    assert editer(edition.yaml, inverse).yaml == MELANGE


# La console (S16-12) émet ces opérations : chacun de ses gestes doit passer la porte du cœur. Le même
# fichier est lu par `apps/web/tests/workflow-edition.test.tsx`, qui vérifie que la console les émet.
GESTES = json.loads(
    (Path(__file__).parents[3] / "apps/web/tests/fixtures/gestes-de-la-console.json").read_text("utf-8")
)


@pytest.mark.parametrize(
    "geste", GESTES["gestes"], ids=[f"{g['geste']}-{i}" for i, g in enumerate(GESTES["gestes"])]
)
def test_chaque_geste_de_la_console_se_greffe_et_se_defait(geste: dict[str, Any]) -> None:
    texte = GESTES["workflow"]
    edition = editer(texte, [geste["operation"]])
    # Greffé, pas forcément valide (retirer la seule transition vers un état le laisse orphelin) :
    # la validité, l'API la dit à côté du texte, sans refuser le geste.
    assert edition.yaml != texte
    assert isinstance(yaml.safe_load(edition.yaml), dict)
    inverse = [o.model_dump(by_alias=True, exclude_none=True) for o in edition.inverse]
    assert editer(edition.yaml, inverse).yaml == texte
