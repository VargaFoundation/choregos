"""Ce qu'un workflow demande au train qu'il prend (ADR 0041, S21-21).

`train: {env: prod, approval: captain}` était écrit et lu par personne ; le webhook de GitHub
embarquait en prod tout ticket fusionné, une étude comprise.
"""

from __future__ import annotations

from choregos_core import parse_workflow
from choregos_core.dsl import load_template
from choregos_core.dsl.trains import approbation_du_train, env_du_train, train_apres_fusion

ETUDE = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: study, version: 1 }
actors:
  architect: { type: agent, role: architect, model: "profile:standard" }
  platform: { type: system }
states:
  inbox: { display: Inbox, kind: wait }
  written: { display: ADR written }
  pr_open: { display: PR open }
  merged: { display: Merged, terminal: true }
transitions:
  - { id: t-write, from: inbox, to: written, by: architect, outputs: [adr_markdown] }
  - { id: t-pr, from: written, to: pr_open, by: platform, does: open_pr }
  - { id: t-merge, from: pr_open, to: merged, by: platform, does: merge_pr, gates: [ci_green] }
"""

LIVRAISON = """apiVersion: choregos/v1
kind: Workflow
metadata: { name: delivery, version: 1 }
actors:
  developer: { type: agent, role: implement, model: "profile:standard" }
  platform: { type: system }
  owner: { type: human, group: maintainers, sla_hours: 8 }
  robot: { type: agent, role: verify_prod, model: "profile:standard" }
states:
  inbox: { display: Inbox, kind: wait }
  done: { display: Done }
  shipped: { display: Shipped }
  live: { display: Live, production: true }
  checked: { display: Checked, terminal: true }
  merged_by_hand: { display: Merged by hand }
transitions:
  - { id: t-human-merge, from: inbox, to: merged_by_hand, by: owner }
  - { id: t-hand-train, from: merged_by_hand, to: live, via: release_train, train: { env: staging } }
  - { id: t-dev, from: inbox, to: done, by: developer, outputs: [summary] }
  - { id: t-ship, from: done, to: shipped, by: platform, does: merge_pr }
  - { id: t-live, from: shipped, to: live, via: release_train, train: { env: prod, approval: robot } }
  - { id: t-check, from: live, to: checked, by: robot, outputs: [verdict] }
"""


def test_le_gabarit_par_defaut_part_en_prod_sous_l_approbation_des_capitaines() -> None:
    workflow = load_template("default-simple")
    train = train_apres_fusion(workflow)
    assert train is not None and train.id == "t-deploy"
    assert env_du_train(train) == "prod"
    assert approbation_du_train(workflow, train) == {"required": True, "group": "release-captains"}


def test_une_livraison_prudente_passe_d_abord_par_staging_sans_approbation() -> None:
    workflow = load_template("advanced")
    train = train_apres_fusion(workflow)
    assert train is not None and env_du_train(train) == "staging"
    assert approbation_du_train(workflow, train) == {"required": False, "group": None}


def test_une_etude_qui_fusionne_un_adr_ne_prend_aucun_train() -> None:
    assert train_apres_fusion(parse_workflow(ETUDE, strict=False)[0]) is None


def test_seule_une_fusion_de_la_plateforme_compte_et_l_approbation_d_un_non_humain_reste_exigee() -> None:
    """`t-ship` fusionne par `does`, sans état `merged*` ; `merged_by_hand`, déclaré AVANT, est atteint
    par un humain : ce n'est pas une fusion de la plateforme, son train n'est pas celui du webhook.
    L'approbation nommée par un agent est exigée, sans groupe — celui de la politique décidera."""
    workflow = parse_workflow(LIVRAISON, strict=False)[0]
    train = train_apres_fusion(workflow)
    assert train is not None and train.id == "t-live"
    assert approbation_du_train(workflow, train) == {"required": True, "group": None}

    sans_does = parse_workflow(LIVRAISON.replace(", does: merge_pr", ""), strict=False)[0]
    assert train_apres_fusion(sans_does) is None, "ni `does` ni fusion par la plateforme : pas de train"


def test_un_train_sans_approval_n_exige_rien_du_depart() -> None:
    workflow = parse_workflow(LIVRAISON.replace(", approval: robot", ""), strict=False)[0]
    train = train_apres_fusion(workflow)
    assert train is not None and env_du_train(train) == "prod"
    assert approbation_du_train(workflow, train) == {"required": False, "group": None}
