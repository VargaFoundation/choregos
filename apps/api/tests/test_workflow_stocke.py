"""Un workflow stocké commence-t-il encore là où il le dit ?

Le DSL pose une règle simple : **le premier état déclaré est l'état initial** (§1.4), et
`Workflow.initial_state` la lisait comme `next(iter(self.states))` — l'ordre du dictionnaire.

PostgreSQL `jsonb` **ne préserve pas l'ordre des clés** : il les range par longueur, puis par
octets. Un workflow rangé dans la colonne `json` de `workflow_defs` en ressort donc dans un autre
ordre que celui qu'on a écrit, et « le premier état déclaré » devient « le nom d'état le plus
court ». Constaté le 2026-09-27 sur le locataire : un ticket créé avec un workflow dont le premier
état s'appelait `inbox` et l'état terminal `fini` est né **dans l'état terminal**. L'interpréteur a
vu un état final, a fermé le ticket, et n'a exécuté aucune étape — 1,5 seconde, zéro run, aucune
anomalie signalée nulle part.

Les trois templates livrés y échappent **par chance** : dans `default-simple`, `inbox` est à la
fois le premier déclaré et le plus court à égalité alphabétique. C'est pourquoi personne ne l'avait
vu.

Le test le plus important est le dernier : il fait l'aller-retour par une vraie base.
"""

from __future__ import annotations

from choregos_core import parse_workflow

from .conftest import sans_postgres

#: Un workflow dont le premier état déclaré n'est PAS le nom le plus court. `fin` (3) passerait
#: devant `commence` (8) dans l'ordre de `jsonb`.
WORKFLOW = """
apiVersion: choregos/v1
kind: Workflow
metadata: { name: ordre, version: 1 }
actors:
  agent_a: { type: agent, role: implement, model: "profile:standard" }
  humain: { type: human, group: org-admins, sla_hours: 24 }
states:
  commence: { display: Commence, kind: wait }
  milieu: { display: Milieu }
  fin: { display: Fin, terminal: true }
transitions:
  - id: t-faire
    from: commence
    to: milieu
    by: agent_a
    outputs: [rapport]
    gates: [outputs_present]
    on_fail: { to: commence, max_attempts: 2, escalate_to: fin }
  - id: t-clore
    from: milieu
    to: fin
    by: humain
    timeout_hours: 72
"""


def test_le_yaml_dit_ou_commencer() -> None:
    """La référence : lu du YAML, l'ordre est celui qu'on a écrit."""
    wf, rapport = parse_workflow(WORKFLOW, strict=False)
    assert rapport.valid, [f"{e.code}: {e.message}" for e in rapport.errors]
    assert wf.initial_state == "commence"


def test_un_aller_retour_json_ne_deplace_pas_le_depart() -> None:
    """Sans base : on simule le rangement de `jsonb` (longueur, puis octets)."""
    wf, _ = parse_workflow(WORKFLOW, strict=False)
    document = wf.model_dump(mode="json")
    assert document["initial"] == "commence", "le parseur doit écrire le départ dans le document"
    document["states"] = dict(sorted(document["states"].items(), key=lambda paire: (len(paire[0]), paire[0])))
    assert next(iter(document["states"])) == "fin", "le test doit vraiment mélanger l'ordre"

    from choregos_contracts import Workflow

    relu = Workflow.model_validate(document)
    assert relu.initial_state == "commence", (
        f"le workflow croit commencer à `{relu.initial_state}` après un aller-retour JSON. "
        "Un ticket créé ainsi naît dans son état terminal."
    )


def test_un_workflow_ne_peut_pas_commencer_par_sa_fin() -> None:
    """La garde qui aurait transformé ce silence en refus."""
    casse = WORKFLOW.replace(
        "metadata: { name: ordre, version: 1 }", "metadata: { name: ordre, version: 1 }\ninitial: fin"
    )
    _, rapport = parse_workflow(casse, strict=False)
    codes = [e.code for e in rapport.errors]
    assert "workflow.initial_state_terminal" in codes, f"attendu un refus, obtenu {codes}"


@sans_postgres
async def test_par_une_vraie_base_le_depart_survit(pg_app: object) -> None:
    """Le test qui compte : `jsonb` range les clés, et le départ doit tenir quand même."""
    from choregos_api.db.models import Organization, Project, WorkflowDef
    from choregos_api.db.session import session_scope
    from choregos_api.services.definitions import workflow_model
    from choregos_contracts import Workflow
    from sqlalchemy import select

    wf, _ = parse_workflow(WORKFLOW, strict=False)
    async with session_scope(orgs="*") as session:
        org = Organization(slug="ordre", name="Ordre")
        session.add(org)
        await session.flush()
        projet = Project(
            org_id=org.id,
            slug="ordre",
            name="Ordre",
            status="active",
            config={"slug": "ordre", "org": "ordre"},
        )
        session.add(projet)
        await session.flush()
        ligne = WorkflowDef(
            project_id=projet.id,
            name="ordre",
            version=1,
            yaml=WORKFLOW,
            json_doc=wf.model_dump(mode="json"),
            checksum="essai",
            is_active=True,
        )
        session.add(ligne)
        await session.flush()
        identifiant = ligne.id

    async with session_scope(orgs="*") as session:
        relue = (await session.execute(select(WorkflowDef).where(WorkflowDef.id == identifiant))).scalar_one()
        ordre = list(relue.json_doc["states"])
        assert ordre[0] == "fin", f"jsonb devrait avoir déplacé les clés, ordre obtenu : {ordre}"
        assert Workflow.model_validate(relue.json_doc).initial_state == "commence"
        assert workflow_model(relue).initial_state == "commence", (
            "c'est ce chemin-là que l'API et l'orchestrateur empruntent"
        )
