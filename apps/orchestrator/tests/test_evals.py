"""Évals : les tickets de référence sont réellement joués, pas seulement déclarés."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_orchestrator.activities import evals

from .conftest import Fixture

TICKETS = sorted(evals.TICKETS.glob("*.yaml"))


def test_il_y_a_des_tickets_de_reference_et_des_depots_jouets() -> None:
    assert TICKETS, "S12-01 : les tickets de référence sont livrés dans tests/fixtures/tickets"
    repos = {p.name for p in evals.REPOS.iterdir() if p.is_dir()}
    assert {"python-toy", "node-toy"} <= repos


@pytest.mark.parametrize("path", TICKETS, ids=lambda p: p.stem)
def test_chaque_ticket_est_bien_forme(path: Path) -> None:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert spec["id"] == path.stem, "l'identifiant du ticket porte le nom du fichier"
    assert (evals.REPOS / spec["repo"]).is_dir()
    assert spec["prompt"].strip() and spec["title"].strip()
    assert spec["allowed_paths"], "un ticket accorde un périmètre d'écriture explicite"
    assert spec["assertions"], "un ticket sans assertion ne mesure rien"
    for assertion in spec["assertions"]:
        assert assertion["kind"] in {
            "file_exists",
            "file_absent",
            "file_unchanged",
            "file_matches",
            "command",
        }
    scripted = spec.get("scripted", {})
    assert "with_memory" in scripted, "il faut une modification scriptée pour la CI"
    for variant in scripted.values():
        for relative in variant.get("files") or {}:
            assert any(_matches(relative, motif) for motif in spec["allowed_paths"]), (
                f"{relative} sort du périmètre accordé par le ticket"
            )


def _matches(relative: str, motif: str) -> bool:
    from fnmatch import fnmatch

    return fnmatch(relative, motif) or fnmatch(relative, motif.replace("**", "*"))


async def test_la_liste_des_fixtures_est_celle_du_depot(setup: Fixture) -> None:
    assert await evals.list_fixtures({}) == [p.stem for p in TICKETS]


async def test_une_cellule_avec_memoire_valide_les_tickets(setup: Fixture) -> None:
    cell = await evals.run_eval_cell(
        {
            "project_slug": setup.project_slug,
            "backend": "openhands",
            "model": "platform/standard",
            "with_memory": True,
            "fixtures": [p.stem for p in TICKETS],
        }
    )
    echecs = [d for d in cell["details"] if d.get("ok") is False]
    assert not echecs, echecs
    assert cell["cases"] >= 5
    assert cell["success_rate"] == 1.0
    assert cell["validated"]
    assert cell["median_cost_usd"] and cell["median_duration_s"]


async def test_sans_memoire_la_convention_projet_est_perdue(setup: Fixture) -> None:
    """Sans mémoire, l'agent écrit du flottant là où le projet compte en centimes."""
    cell = await evals.run_eval_cell(
        {
            "project_slug": setup.project_slug,
            "backend": "openhands",
            "model": "platform/standard",
            "with_memory": False,
            "fixtures": ["py-remise-volume"],
        }
    )
    assert cell["success_rate"] == 0.0
    assert not cell["validated"]
    assert "float" in cell["details"][0]["reason"]


async def test_un_ticket_pige_echoue_si_l_agent_sort_du_perimetre(setup: Fixture, tmp_path: Path) -> None:
    """Le ticket hors périmètre n'est réussi que si rien n'est écrit hors du périmètre."""
    spec = yaml.safe_load((evals.TICKETS / "py-hors-perimetre.yaml").read_text(encoding="utf-8"))
    desobeissant = {
        **spec,
        "scripted": {"with_memory": {"files": {"deploy/prod.yaml": "bus: prod\n"}, "status": "done"}},
    }
    outcome = await evals._play(desobeissant, {"project_slug": setup.project_slug, "with_memory": True})
    assert not outcome["ok"]
    assert "deploy/prod.yaml" in outcome["reason"] or "file_absent" in outcome["reason"]


async def test_un_outil_absent_ignore_le_cas_sans_le_compter_reussi(setup: Fixture) -> None:
    spec: dict[str, Any] = {
        "id": "factice",
        "repo": "python-toy",
        "assertions": [{"kind": "command", "run": "licorne --version", "requires": "licorne-cli"}],
        "scripted": {"with_memory": {"files": {}}},
    }
    outcome = await evals._play(spec, {"project_slug": setup.project_slug, "with_memory": True})
    assert outcome["skipped"] and "licorne-cli" in outcome["reason"]


async def test_une_commande_rouge_fait_echouer_la_cellule(setup: Fixture) -> None:
    spec: dict[str, Any] = {
        "id": "factice-rouge",
        "repo": "python-toy",
        "assertions": [{"kind": "command", "run": "exit 3"}],
        "scripted": {"with_memory": {"files": {}}},
    }
    outcome = await evals._play(spec, {"project_slug": setup.project_slug, "with_memory": True})
    assert not outcome["ok"] and not outcome["skipped"]


async def test_le_depot_jouet_n_est_jamais_modifie_sur_place(setup: Fixture) -> None:
    avant = (evals.REPOS / "python-toy" / "src" / "panier" / "panier.py").read_text(encoding="utf-8")
    await evals.run_eval_cell(
        {
            "project_slug": setup.project_slug,
            "backend": "openhands",
            "model": "platform/standard",
            "with_memory": True,
            "fixtures": ["py-bug-arrondi"],
        }
    )
    apres = (evals.REPOS / "python-toy" / "src" / "panier" / "panier.py").read_text(encoding="utf-8")
    assert avant == apres, "les évals travaillent dans une copie jetable"
