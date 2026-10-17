"""Évals : les tickets de référence sont réellement joués, pas seulement déclarés."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from choregos_orchestrator.activities import evals

from .conftest import Fixture

TICKETS = sorted(evals.TICKETS.glob("*.yaml"))


def _node_major() -> int | None:
    """The installed Node's major version, or `None` without Node."""
    node = shutil.which("node")
    if node is None:
        return None
    found = re.match(
        r"v(\d+)", subprocess.run([node, "--version"], capture_output=True, text=True, check=False).stdout
    )
    return int(found.group(1)) if found else None


#: The node toy repos run `node --test "test/**/*.test.js"`: glob patterns need Node 21+ (CI uses 24).
#: Under an older Node the command answers "Could not find …/*.test.js", which reads as a product bug.
needs_node_with_globs = pytest.mark.skipif(
    (_node_major() or 0) < 22,
    reason="the node toy repos run `node --test` with a glob: needs Node >= 22 (CI uses 24)",
)


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


@needs_node_with_globs
async def test_une_cellule_avec_memoire_valide_les_tickets(setup: Fixture) -> None:
    cell = await evals.run_eval_cell(
        {
            "project_slug": setup.project_slug,
            "backend": "codex",
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


@needs_node_with_globs
async def test_sans_memoire_la_convention_projet_est_perdue(setup: Fixture) -> None:
    """Sans mémoire, l'agent écrit du flottant là où le projet compte en centimes."""
    cell = await evals.run_eval_cell(
        {
            "project_slug": setup.project_slug,
            "backend": "codex",
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
            "backend": "codex",
            "model": "platform/standard",
            "with_memory": True,
            "fixtures": ["py-bug-arrondi"],
        }
    )
    apres = (evals.REPOS / "python-toy" / "src" / "panier" / "panier.py").read_text(encoding="utf-8")
    assert avant == apres, "les évals travaillent dans une copie jetable"


# ── S37-09: the context reduction as a dimension, tokens from the ledger (ADR 0055) ──

PLAYABLE = ["py-bug-arrondi", "py-remise-volume", "py-test-manquant"]


def _usage(name: str, context: str) -> dict[str, int]:
    spec = yaml.safe_load((evals.TICKETS / f"{name}.yaml").read_text(encoding="utf-8"))
    return dict(spec["scripted_usage"][context])


@pytest.mark.parametrize("path", TICKETS, ids=lambda p: p.stem)
def test_every_ticket_scripts_its_usage_for_the_four_contexts(path: Path) -> None:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    usage = spec["scripted_usage"]
    assert set(usage) == set(evals.CONTEXTS)
    for numbers in usage.values():
        assert set(numbers) == {"tokens_in", "tokens_cached", "tokens_out", "turns"}
        assert numbers["tokens_cached"] <= numbers["tokens_in"], "the gateway counts cache reads as input"


async def test_a_fake_matrix_has_four_context_cells_with_tokens_from_the_ledger(setup: Fixture) -> None:
    import statistics

    cells = [
        await evals.run_eval_cell(
            {
                "project_slug": setup.project_slug,
                "backend": "claude-code",
                "model": "platform/standard",
                "with_memory": True,
                "context": context,
                "fixtures": PLAYABLE,
            }
        )
        for context in evals.CONTEXTS
    ]
    assert [cell["context"] for cell in cells] == list(evals.CONTEXTS)
    for cell in cells:
        expected = [_usage(name, cell["context"]) for name in PLAYABLE]
        assert cell["cases"] == cell["metered"] == len(PLAYABLE)
        for column, key in [
            ("median_tokens_in", "tokens_in"),
            ("median_tokens_cached", "tokens_cached"),
            ("median_tokens_out", "tokens_out"),
            ("median_turns", "turns"),
        ]:
            assert cell[column] == statistics.median(u[key] for u in expected), (cell["context"], column)
        assert cell["median_cost_usd"] and cell["success_rate"] == 1.0
    costs = [cell["median_cost_usd"] for cell in cells]
    assert costs == sorted(costs, reverse=True), "the scripted usage shrinks from none to rtk+headroom"
    gateway = setup.adapters.gateway
    assert not gateway.keys, "every ticket's key is revoked once played"
    assert len(gateway.revoked) == len(PLAYABLE) * len(evals.CONTEXTS), "one key per ticket and context"


async def test_the_cell_reports_what_the_ledger_says_not_what_the_fixture_scripts(setup: Fixture) -> None:
    """A gateway that cannot be billed by hand is the real one: its ledger is the only measure."""
    from choregos_core.domain import Spend

    class Ledger:
        """A gateway without `record_usage`: it only says what it saw."""

        def __init__(self, seen: Spend) -> None:
            self.seen = seen
            self.minted = 0

        async def mint_key(
            self, metadata: dict[str, Any], budget_usd: float, ttl_s: int, models: list[str]
        ) -> Any:
            from types import SimpleNamespace

            self.minted += 1
            return SimpleNamespace(key_id=f"real-{self.minted}")

        async def spend(self, key_id: str) -> Spend:
            return self.seen

        async def revoke(self, key_id: str) -> None:
            return None

    payload = {
        "project_slug": setup.project_slug,
        "backend": "claude-code",
        "model": "platform/standard",
        "with_memory": True,
        "context": "headroom",
        "fixtures": ["py-bug-arrondi"],
    }
    setup.adapters.gateway = Ledger(
        Spend(tokens_in=7_000, tokens_cached=3_000, tokens_out=500, cost_usd=0.04, requests=3)
    )
    cell = await evals.run_eval_cell(payload)
    assert (cell["median_tokens_in"], cell["median_tokens_cached"], cell["median_tokens_out"]) == (
        7000,
        3000,
        500,
    )
    assert cell["median_cost_usd"] == 0.04
    assert cell["median_turns"] is None, "turns are scripted, and a real gateway billed nothing scripted"

    setup.adapters.gateway = Ledger(Spend())
    silent = await evals.run_eval_cell(payload)
    assert silent["cases"] == 1 and silent["metered"] == 0
    assert silent["median_tokens_in"] is None and silent["median_cost_usd"] is None, (
        "no measure, never a guess"
    )


@pytest.mark.parametrize("make_dir", [True, False], ids=["empty", "missing"])
async def test_no_reference_ticket_fails_the_matrix_without_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, make_dir: bool
) -> None:
    """S37-13: an image without the fixtures used to give `[]`, and the matrix published empty cells."""
    from temporalio.exceptions import ApplicationError

    tickets = tmp_path / "tickets"
    if make_dir:
        tickets.mkdir()
    monkeypatch.setattr(evals, "TICKETS", tickets)
    with pytest.raises(ApplicationError) as refused:
        await evals.list_fixtures({})
    assert refused.value.non_retryable and refused.value.type == "EvalFixturesMissing"


async def test_an_unknown_context_fails_the_cell_without_retry(setup: Fixture) -> None:
    from temporalio.exceptions import ApplicationError

    with pytest.raises(ApplicationError) as refused:
        await evals.run_eval_cell(
            {
                "project_slug": setup.project_slug,
                "backend": "claude-code",
                "model": "platform/standard",
                "with_memory": True,
                "context": "zip",
                "fixtures": PLAYABLE,
            }
        )
    assert refused.value.non_retryable and "rtk+headroom" in str(refused.value)


async def test_a_cell_without_context_is_the_none_cell(setup: Fixture) -> None:
    """A matrix started before S37-09 sends no context: its cells are the `none` ones."""
    cell = await evals.run_eval_cell(
        {
            "project_slug": setup.project_slug,
            "backend": "claude-code",
            "model": "platform/standard",
            "with_memory": True,
            "fixtures": ["py-bug-arrondi"],
        }
    )
    assert cell["context"] == "none"
    assert cell["median_tokens_in"] == _usage("py-bug-arrondi", "none")["tokens_in"]


async def test_a_backend_is_validated_by_its_cells_without_context_reduction(setup: Fixture) -> None:
    from choregos_api.db.models import ModelProfileRow
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    cell = {"model": "platform/standard", "with_memory": True, "success_rate": 0.9}
    await evals.publish_matrix(
        {
            "project_slug": setup.project_slug,
            "results": [
                {**cell, "backend": "codex", "context": "rtk+headroom", "validated": True},
                {**cell, "backend": "codex", "context": "none", "validated": False},
                {**cell, "backend": "claude-code", "context": "none", "validated": True},
                {**cell, "backend": "opencode", "validated": True},
            ],
        }
    )
    async with session_scope() as session:
        row = (
            await session.execute(
                select(ModelProfileRow).where(ModelProfileRow.litellm_model == "platform/standard")
            )
        ).scalar_one()
    assert row.validated_backends == ["claude-code", "opencode"], "codex only passed with a reduction"
