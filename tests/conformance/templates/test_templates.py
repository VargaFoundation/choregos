"""Conformité des templates : ce qu'un template promet doit exister.

Un template qui déclare un connecteur inexistant ou une étape que personne n'implémente
échoue au provisioning, chez un client, au pire moment. Cette suite le dit avant.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.conformance

TEMPLATES = Path(__file__).resolve().parents[3] / "templates"
MANIFESTS = sorted(TEMPLATES.glob("*/manifest.yaml"))

# Étapes connues de `provisioning._execute`, plus celles explicitement ignorées.
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
    assert MANIFESTS, "aucun template livré"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_le_manifeste_est_bien_forme(path: Path) -> None:
    doc = manifest(path)
    assert doc["apiVersion"] == "choregos/v1" and doc["kind"] == "Template"
    metadata = doc["metadata"]
    assert metadata["name"] == path.parent.name, "le nom du template est celui du dossier"
    assert metadata["version"] and metadata["display"] and metadata["description"].strip()


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_les_connecteurs_declares_existent(path: Path) -> None:
    """Un template ne promet que des adaptateurs enregistrés."""
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
        assert name in KNOWN_STEPS, f"étape inconnue dans {path.parent.name} : {name}"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_le_workflow_et_la_politique_par_defaut_existent(path: Path) -> None:
    from choregos_core import load_preset
    from choregos_core.dsl import load_template

    defaults = manifest(path)["defaults"]
    workflow_ref = str(defaults["workflow"])
    assert workflow_ref.startswith("template:")
    name = workflow_ref.removeprefix("template:").split("@")[0]
    assert load_template(name).metadata.name == name

    policy_ref = str(defaults["policy"])
    assert policy_ref.startswith("preset:")
    assert load_preset(policy_ref.removeprefix("preset:")) is not None


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_le_scaffold_existe_et_contient_ce_que_le_manifeste_annonce(path: Path) -> None:
    doc = manifest(path)
    scaffold = (path.parent / str(doc.get("scaffold", "./scaffold")).lstrip("./")).resolve()
    assert scaffold.is_dir(), f"dossier de scaffold absent : {scaffold}"

    annonces: list[str] = []
    for step in doc.get("steps", []):
        if isinstance(step, dict) and "repo.scaffold_pr" in step:
            annonces = list((step["repo.scaffold_pr"] or {}).get("files", []))
    for fichier in annonces:
        candidats = [scaffold / fichier, scaffold / f"{fichier}.j2"]
        assert any(c.exists() for c in candidats), f"{fichier} annoncé mais absent du scaffold"


@pytest.mark.parametrize("path", MANIFESTS, ids=lambda p: p.parent.name)
def test_les_entrees_obligatoires_sont_nommees_et_decrites(path: Path) -> None:
    for entry in manifest(path).get("inputs", []):
        assert entry.get("name") and entry.get("type")
        if entry.get("required"):
            assert entry.get("description"), f"entrée obligatoire sans description : {entry['name']}"
