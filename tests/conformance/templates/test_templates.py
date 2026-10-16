"""Template conformance: what a template promises must exist.

A template that declares a nonexistent connector or a step nobody implements fails at
provisioning, at a customer's, at the worst moment. This suite says so beforehand.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.conformance

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
MANIFESTS = sorted(TEMPLATES.glob("*/manifest.yaml"))

# Steps known to `provisioning._execute`, plus those explicitly ignored.
KNOWN_STEPS = {
    "github.install_app",
    "github.ensure_labels",
    "github.ensure_project_board",
    "github.ensure_issue_template",
    "github.ensure_webhooks",
    "aca.check_environment",
    "aca.check_identity",
    "gitops.write_project_manifests",
    "gitops.open_pr_or_commit",
    "argocd.wait_synced",
    "repo.scaffold_pr",
    "memory.create_tenant",
    "memory.initial_import",
    "gateway.create_team_and_budget",
    "notify.test_message",
}


def manifest(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_il_y_a_des_templates() -> None:
    assert MANIFESTS, "no template shipped"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_le_manifeste_est_bien_forme(path: Path) -> None:
    doc = manifest(path)
    assert doc["apiVersion"] == "choregos/v1" and doc["kind"] == "Template"
    metadata = doc["metadata"]
    assert metadata["name"] == path.parent.name, "the template name is the folder name"
    assert metadata["version"] and metadata["display"] and metadata["description"].strip()


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_les_connecteurs_declares_existent(path: Path) -> None:
    """A template only promises registered adapters."""
    import os

    from choregos_adapters import available

    was_fake = os.environ.pop("CHOREGOS_FAKES", None)
    try:
        for kind, type_name in manifest(path)["requires"]["connectors"].items():
            assert type_name in available(kind), (
                f"{path.parent.name} demande `{kind}: {type_name}`, connus : {', '.join(available(kind))}"
            )
    finally:
        if was_fake is not None:
            os.environ["CHOREGOS_FAKES"] = was_fake


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_les_etapes_sont_implementees(path: Path) -> None:
    for step in manifest(path).get("steps", []):
        name = step if isinstance(step, str) else next(iter(step))
        assert name in KNOWN_STEPS, f"unknown step in {path.parent.name}: {name}"


def _workflows(path: Path) -> list[Any]:
    """The workflows the template ships, read and validated as at a project's birth."""
    from choregos_core.dsl import load_template, parse_workflow

    defaults = manifest(path)["defaults"]
    refs = list(defaults.get("workflows") or [defaults["workflow"]])
    livres = []
    for ref in refs:
        if str(ref).startswith("template:"):
            livres.append(load_template(str(ref).removeprefix("template:").split("@")[0]))
            continue
        chemin = (path.parent / str(ref)).resolve()
        assert chemin.is_relative_to(path.parent.resolve()), f"{ref} leaves the template folder"
        workflow, rapport = parse_workflow(chemin.read_text(encoding="utf-8"), strict=False)
        assert rapport.valid, f"{path.parent.name}/{ref} : {rapport.as_dict()['errors']}"
        assert not rapport.warnings, f"{path.parent.name}/{ref} : {rapport.as_dict()['warnings']}"
        livres.append(workflow)
    return livres


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_les_workflows_le_defaut_le_routage_et_la_politique_existent(path: Path) -> None:
    from choregos_core import load_preset

    defaults = manifest(path)["defaults"]
    noms = [w.metadata.name for w in _workflows(path)]
    assert len(set(noms)) == len(noms), f"two workflows with the same name: {noms}"
    assert defaults.get("default_workflow", noms[0]) in noms
    for regle in defaults.get("routing") or []:
        assert regle["workflow"] in noms, (
            f"the routing targets `{regle['workflow']}`, which the template does not ship"
        )

    policy_ref = str(defaults["policy"])
    if policy_ref.startswith("preset:"):
        assert load_preset(policy_ref.removeprefix("preset:")) is not None
        return
    # A policy of the template's own (S21-22): in its folder, and readable.
    from choregos_core.policy import parse_policy

    chemin = (path.parent / policy_ref).resolve()
    assert chemin.is_relative_to(path.parent.resolve()), f"{policy_ref} leaves the template folder"
    assert parse_policy(chemin.read_text(encoding="utf-8")).metadata.name


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_le_scaffold_existe_et_contient_ce_que_le_manifeste_annonce(path: Path) -> None:
    doc = manifest(path)
    annonces: list[str] = []
    for step in doc.get("steps", []):
        if isinstance(step, dict) and "repo.scaffold_pr" in step:
            annonces = list((step["repo.scaffold_pr"] or {}).get("files", []))
    if not annonces and "scaffold" not in doc:
        return  # a template without a repository scaffolds nothing
    scaffold = (path.parent / str(doc.get("scaffold", "./scaffold")).lstrip("./")).resolve()
    assert scaffold.is_dir(), f"dossier de scaffold absent : {scaffold}"
    for fichier in annonces:
        candidats = [scaffold / fichier, scaffold / f"{fichier}.j2"]
        assert any(c.exists() for c in candidats), f"{fichier} announced but missing from the scaffold"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_les_actions_n_appellent_que_ce_que_le_gabarit_annonce(path: Path) -> None:
    """Every `connector.call` names a connector the manifest announces (`org_connectors`), an
    operation its capability declares, with the arguments its schema requires — and nothing else. An
    `mcp` server discovers its tools: only the connector name is judged. A template only calls
    core effects: a plugin's are not everywhere."""
    from choregos_adapters import connector_types

    annonces = dict(manifest(path)["requires"].get("org_connectors") or {})
    operations = {
        kind: {o.name: o for o in spec.operations} for kind, _, spec in connector_types() if spec.operations
    }
    for workflow in _workflows(path):
        for transition in workflow.transitions:
            action = transition.action
            if action is None:
                continue
            for effet in action.effects:
                specs = [effet.model_dump(by_alias=True), *([effet.compensate] if effet.compensate else [])]
                for spec in specs:
                    nom = str(spec["effect"])
                    assert nom in {"connector.call", "check", "verifier"}, (
                        f"{transition.key}: effect `{nom}` outside the core"
                    )
                    if nom != "connector.call":
                        continue
                    appel = dict(spec.get("with") or {})
                    connecteur = appel["connector"]
                    ou = f"{workflow.metadata.name}/{transition.key} : {connecteur}"
                    assert connecteur in annonces, f"{ou} is not announced in `requires.org_connectors`"
                    capacite = annonces[connecteur]
                    if capacite == "mcp":
                        continue
                    operation = operations.get(capacite, {}).get(appel["operation"])
                    assert operation is not None, (
                        f"{ou} : `{appel['operation']}` n'est pas une opération de `{capacite}`"
                    )
                    schema = operation.input_schema or {}
                    arguments = set(dict(appel.get("arguments") or {}))
                    manquants = set(schema.get("required", [])) - arguments
                    assert not manquants, f"{ou}/{appel['operation']} : il manque {sorted(manquants)}"
                    en_trop = arguments - set(schema.get("properties", {}))
                    assert not en_trop, f"{ou}/{appel['operation']}: {sorted(en_trop)} outside the schema"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_les_agents_et_les_skills_du_gabarit_sont_installables(path: Path) -> None:
    from choregos_api.schemas.agents import AgentCreate
    from choregos_api.services.agents import erreurs_d_une_version
    from choregos_api.services.gabarits import livraison

    livree = livraison(manifest(path), path.parent)
    from choregos_api.services.skills import valider

    skills = {valider(fichiers).name for fichiers in livree.skills}
    agents = set()
    for document in livree.agents:
        agent = AgentCreate.model_validate(document)
        erreurs_d_une_version(agent.spec)
        agents.add(agent.slug)
        orphelines = {s.slug for s in agent.spec.skills} - skills
        assert not orphelines, f"{agent.slug} names skills the template does not ship: {sorted(orphelines)}"
    for workflow in _workflows(path):
        for nom, acteur in workflow.actors.items():
            reference = getattr(acteur, "agent", None)
            if reference:
                slug = reference.split("@")[0]
                assert slug in agents, (
                    f"{workflow.metadata.name}/{nom} names agent `{slug}`, which the template does not ship"
                )


#: The extensions a template may ship (S20-09): any other would have nobody to validate it
#: before a customer — nor, probably, to install it.
EXTENSIONS_CONNUES = {"ontology"}
#: What an ontology proof can collect outside the SCM (`connector.query` only reads its PRs).
PREUVES_SERVIES = {"object.reread", "collector.rerun"}


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_les_extensions_du_gabarit_sont_valides_pour_leur_greffon(path: Path, tmp_path: Path) -> None:
    """A shipped ontology compiles with no error NOR warning against what the plugin can play; its
    effects and proofs are served; an agent it authorises to propose is shipped by the template."""
    from choregos_api.schemas.agents import AgentCreate
    from choregos_api.services.gabarits import livraison

    livree = livraison(manifest(path), path.parent)
    inconnues = set(livree.extensions) - EXTENSIONS_CONNUES
    assert not inconnues, f"extensions nobody validates: {sorted(inconnues)}"
    if "ontology" not in livree.extensions:
        return
    from choregos_ontology.compiler import compile_directory
    from choregos_ontology.service.actions import SERVED_EFFECTS
    from choregos_ontology.service.api import REGISTRY, _check_package, _write_package

    fichiers = livree.extensions["ontology"]
    _check_package(fichiers)
    _write_package(tmp_path, fichiers)
    compilee, validation = compile_directory(tmp_path, REGISTRY)
    assert compilee is not None and validation.valid, [i.format() for i in validation.errors]
    assert not validation.warnings, [i.format() for i in validation.warnings]
    agents = {AgentCreate.model_validate(document).slug for document in livree.agents}
    for action in compilee.to_dict()["action_types"]:
        effets = {e["type"] for e in action["effects"]} - SERVED_EFFECTS
        assert not effets, f"{action['name']}: effects the plugin does not execute: {sorted(effets)}"
        preuves = {p["collect"]["type"] for p in action["evidence"]} - PREUVES_SERVIES
        assert not preuves, f"{action['name']}: proofs the plugin does not collect: {sorted(preuves)}"
        absents = {p.removeprefix("agent:") for p in action["propose"] if p.startswith("agent:")} - agents
        assert not absents, f"{action['name']}: agents the template does not ship: {sorted(absents)}"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_les_entrees_obligatoires_sont_nommees_et_decrites(path: Path) -> None:
    for entry in manifest(path).get("inputs", []):
        assert entry.get("name") and entry.get("type")
        if entry.get("required"):
            assert entry.get("description"), f"entrée obligatoire sans description : {entry['name']}"
