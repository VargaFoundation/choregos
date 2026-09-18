"""Tests du rendu de workflow en graphe (React Flow) et en Mermaid."""

from __future__ import annotations

import pytest
from choregos_contracts import Workflow
from choregos_core import parse_workflow
from choregos_core.dsl import TEMPLATE_NAMES, load_template, to_graph, to_mermaid

WILDCARD_YAML = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: joker, version: 1 }
actors:
  dev: { type: agent, role: implement }
  checker: { type: agent, role: verify }
  owner: { type: human, group: po }
states:
  inbox: { display: Inbox }
  doing: { display: Doing }
  needs_human: { display: Question, kind: wait }
  done: { display: Done, terminal: true }
transitions:
  - { id: t1, from: inbox, to: doing, by: dev }
  - { id: t2, from: doing, to: done, by: checker }
  - { id: t3, from: "*agent", to: needs_human, by: owner }
  - { id: t4, from: needs_human, to: done, by: owner }
defaults:
  from_any_agent_state: { on_question: needs_human }
"""


@pytest.fixture(scope="module")
def workflow() -> Workflow:
    return load_template("advanced")


def test_chaque_etat_est_un_noeud_avec_son_couloir(workflow: Workflow) -> None:
    graph = to_graph(workflow)
    assert {n["id"] for n in graph["nodes"]} == set(workflow.states)
    lanes = {n["lane"] for n in graph["nodes"]}
    assert {"agent", "human"} <= lanes
    assert all(n["lane"] == "terminal" for n in graph["nodes"] if n["terminal"])


def test_un_noeud_porte_de_quoi_dessiner_la_carte(workflow: Workflow) -> None:
    node = next(n for n in to_graph(workflow)["nodes"] if n["id"] == workflow.initial_state)
    assert node["display"] and node["kind"]
    assert node["terminal"] is False


def test_les_aretes_pointent_vers_des_etats_connus(workflow: Workflow) -> None:
    for edge in to_graph(workflow)["edges"]:
        assert edge["from"] in workflow.states
        assert edge["to"] in workflow.states


def test_le_joker_est_developpe_en_une_arete_par_etat_agent() -> None:
    wf, _ = parse_workflow(WILDCARD_YAML, strict=False)
    wildcards = [e for e in to_graph(wf)["edges"] if e["wildcard"]]
    expanded = {e["from"] for e in wildcards}
    assert expanded == wf.agent_driven_states()
    assert "*" not in expanded
    assert len({e["id"] for e in wildcards}) == len(wildcards), "des identifiants d'arête en double"


def test_les_etats_d_escalade_sont_relies(workflow: Workflow) -> None:
    edges = to_graph(workflow)["edges"]
    kinds = {e["kind"] for e in edges}
    assert {"nominal", "default"} <= kinds
    questions = [e for e in edges if e["label"] == "question"]
    assert {e["from"] for e in questions} == workflow.agent_driven_states()
    assert {e["to"] for e in questions} == {"needs_human"}


def test_le_retour_apres_rejet_est_une_arete(workflow: Workflow) -> None:
    rejects = [e for e in to_graph(workflow)["edges"] if e["kind"] == "reject"]
    assert rejects, "le template avancé a des approbations humaines rejetables"
    for edge in rejects:
        origin = next(t for t in workflow.transitions if t.from_ == edge["from"] and t.on_reject)
        assert edge["to"] == origin.on_reject


def test_le_train_est_identifie_comme_tel(workflow: Workflow) -> None:
    trains = [e for e in to_graph(workflow)["edges"] if e["via"] == "release_train"]
    assert trains, "le template avancé passe par un train de release"
    assert all(e["actor_type"] == "train" for e in trains)


def test_les_gates_voyagent_avec_l_arete(workflow: Workflow) -> None:
    gated = [e for e in to_graph(workflow)["edges"] if e["gates"]]
    assert gated, "des transitions sont conditionnées par des gates"
    known = {g for t in workflow.transitions for g in t.gate_names()}
    assert all(set(e["gates"]) <= known for e in gated)


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_le_mermaid_est_coherent(name: str) -> None:
    wf = load_template(name)
    mermaid = to_mermaid(wf)
    assert mermaid.startswith("stateDiagram-v2")
    assert f"[*] --> {wf.initial_state}" in mermaid
    for terminal in wf.terminal_states():
        assert f"{terminal} --> [*]" in mermaid
    assert "*" not in mermaid.replace("[*]", "").replace("agent_states", "")


@pytest.mark.parametrize("name", TEMPLATE_NAMES)
def test_le_graphe_de_chaque_template_est_connexe(name: str) -> None:
    wf = load_template(name)
    graph = to_graph(wf)
    reachable = {wf.initial_state}
    edges = [(e["from"], e["to"]) for e in graph["edges"]]
    changed = True
    while changed:
        changed = False
        for src, dst in edges:
            if src in reachable and dst not in reachable:
                reachable.add(dst)
                changed = True
    assert reachable == set(wf.states), f"états injoignables : {set(wf.states) - reachable}"
    assert len({e["id"] for e in graph["edges"]}) == len(graph["edges"]), "identifiants d'arête en double"
