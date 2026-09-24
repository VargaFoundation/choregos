"""Un projet sans dépôt : l'agent travaille dans un répertoire vide (ADR 0012, limite n°1).

Un métier qui instruit des dossiers, qualifie des profils ou rédige des courriers n'a pas
de dépôt. Il devait pourtant en déclarer un, qui ne servait à rien, et le runner clonait
un faux workspace pour promettre une branche que personne ne relirait.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from choregos_contracts import StageInput
from choregos_runner.workspace import Workspace

pytestmark = pytest.mark.asyncio


@pytest.fixture
def sans_depot(stage_input: StageInput) -> StageInput:
    return stage_input.model_copy(update={"repo": None})


async def test_le_workspace_est_un_repertoire_vide_et_versionne(
    sans_depot: StageInput, tmp_path: Path
) -> None:
    """Vide, mais suivi par git : c'est ce qui permet au runner de mesurer ce que l'agent
    a écrit, sans rien cloner ni rien pousser."""
    workspace = Workspace(tmp_path / "travail")
    await workspace.prepare(sans_depot)

    assert (tmp_path / "travail" / ".git").is_dir(), "git local, pour mesurer le travail"
    fichiers = [p.name for p in (tmp_path / "travail").iterdir() if p.name != ".git"]
    assert fichiers == [], f"le répertoire devrait être vide, il contient {fichiers}"

    distants = await workspace.git("remote")
    assert distants.stdout.strip() == "", "aucun distant : il n'y a nulle part où pousser"


async def test_ce_que_l_agent_ecrit_est_vu_comme_ajoute(sans_depot: StageInput, tmp_path: Path) -> None:
    """La référence est l'arbre vide de git : tout est « ajouté », ce qui est la vérité
    pour un répertoire qui n'avait rien."""
    from choregos_runner.runner import ARBRE_VIDE

    workspace = Workspace(tmp_path / "travail")
    await workspace.prepare(sans_depot)
    (tmp_path / "travail" / "dossier.md").write_text("# Dossier instruit\n", encoding="utf-8")
    await workspace.git("add", "-A")
    await workspace.commit("docs: instruire le dossier")

    changes = await workspace.changed_files(ARBRE_VIDE)
    assert changes == ["dossier.md"], changes
    additions, deletions = await workspace.diff_stats(ARBRE_VIDE)
    assert additions == 1 and deletions == 0


async def test_le_contrat_accepte_une_etape_sans_depot(sans_depot: StageInput) -> None:
    """Et le contrat le dit : `repo` est absent, pas rempli d'un dépôt factice."""
    dump = sans_depot.model_dump(mode="json", by_alias=True, exclude_none=True)
    assert "repo" not in dump
    assert StageInput.model_validate(dump).repo is None
