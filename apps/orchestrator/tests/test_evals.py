"""Evals: the reference tickets are really played, not just declared."""

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


def test_there_are_reference_tickets_and_toy_repositories() -> None:
    assert TICKETS, "S12-01: the reference tickets are shipped in tests/fixtures/tickets"
    repos = {p.name for p in evals.REPOS.iterdir() if p.is_dir()}
    assert {"python-toy", "node-toy"} <= repos


@pytest.mark.parametrize("path", TICKETS, ids=lambda p: p.stem)
def test_every_ticket_is_well_formed(path: Path) -> None:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert spec["id"] == path.stem, "the ticket id carries the file name"
    assert (evals.REPOS / spec["repo"]).is_dir()
    assert spec["prompt"].strip() and spec["title"].strip()
    assert spec["allowed_paths"], "a ticket grants an explicit write scope"
    assert spec["assertions"], "a ticket without an assertion measures nothing"
    for assertion in spec["assertions"]:
        assert assertion["kind"] in {
            "file_exists",
            "file_absent",
            "file_unchanged",
            "file_matches",
            "command",
        }
    scripted = spec.get("scripted", {})
    assert "with_memory" in scripted, "a scripted change is needed for the CI"
    for variant in scripted.values():
        for relative in variant.get("files") or {}:
            assert any(_matches(relative, pattern) for pattern in spec["allowed_paths"]), (
                f"{relative} is outside the scope granted by the ticket"
            )


def _matches(relative: str, pattern: str) -> bool:
    from fnmatch import fnmatch

    return fnmatch(relative, pattern) or fnmatch(relative, pattern.replace("**", "*"))


async def test_the_fixture_list_is_the_repository_s(setup: Fixture) -> None:
    assert await evals.list_fixtures({}) == sorted(p.stem for p in TICKETS)


@needs_node_with_globs
async def test_a_cell_with_memory_validates_the_tickets(setup: Fixture) -> None:
    cell = await evals.run_eval_cell(
        {
            "project_slug": setup.project_slug,
            "backend": "codex",
            "model": "platform/standard",
            "with_memory": True,
            "fixtures": [p.stem for p in TICKETS],
        }
    )
    failures = [d for d in cell["details"] if d.get("ok") is False]
    assert not failures, failures
    assert cell["cases"] >= 5
    assert cell["success_rate"] == 1.0
    assert cell["validated"]
    assert cell["median_cost_usd"] and cell["median_duration_s"]


@needs_node_with_globs
async def test_without_memory_the_project_convention_is_lost(setup: Fixture) -> None:
    """Without memory, the agent writes a float where the project counts in cents."""
    cell = await evals.run_eval_cell(
        {
            "project_slug": setup.project_slug,
            "backend": "codex",
            "model": "platform/standard",
            "with_memory": False,
            "fixtures": ["py-volume-discount"],
        }
    )
    assert cell["success_rate"] == 0.0
    assert not cell["validated"]
    assert "float" in cell["details"][0]["reason"]


async def test_a_trap_ticket_fails_if_the_agent_goes_out_of_scope(setup: Fixture, tmp_path: Path) -> None:
    """The out-of-scope ticket succeeds only if nothing is written outside the scope."""
    spec = yaml.safe_load((evals.TICKETS / "py-out-of-scope.yaml").read_text(encoding="utf-8"))
    disobedient = {
        **spec,
        "scripted": {"with_memory": {"files": {"deploy/prod.yaml": "bus: prod\n"}, "status": "done"}},
    }
    outcome = await evals._play(disobedient, {"project_slug": setup.project_slug, "with_memory": True})
    assert not outcome["ok"]
    assert "deploy/prod.yaml" in outcome["reason"] or "file_absent" in outcome["reason"]


async def test_an_absent_tool_skips_the_case_without_counting_it_passed(setup: Fixture) -> None:
    spec: dict[str, Any] = {
        "id": "dummy",
        "repo": "python-toy",
        "assertions": [{"kind": "command", "run": "unicorn --version", "requires": "unicorn-cli"}],
        "scripted": {"with_memory": {"files": {}}},
    }
    outcome = await evals._play(spec, {"project_slug": setup.project_slug, "with_memory": True})
    assert outcome["skipped"] and "unicorn-cli" in outcome["reason"]


async def test_a_red_command_fails_the_cell(setup: Fixture) -> None:
    spec: dict[str, Any] = {
        "id": "dummy-red",
        "repo": "python-toy",
        "assertions": [{"kind": "command", "run": "exit 3"}],
        "scripted": {"with_memory": {"files": {}}},
    }
    outcome = await evals._play(spec, {"project_slug": setup.project_slug, "with_memory": True})
    assert not outcome["ok"] and not outcome["skipped"]


async def test_the_toy_repository_is_never_modified_in_place(setup: Fixture) -> None:
    before = (evals.REPOS / "python-toy" / "src" / "cart" / "cart.py").read_text(encoding="utf-8")
    await evals.run_eval_cell(
        {
            "project_slug": setup.project_slug,
            "backend": "codex",
            "model": "platform/standard",
            "with_memory": True,
            "fixtures": ["py-rounding-bug"],
        }
    )
    after = (evals.REPOS / "python-toy" / "src" / "cart" / "cart.py").read_text(encoding="utf-8")
    assert before == after, "the evals work in a throwaway copy"


# ── S37-09: the context reduction as a dimension, tokens from the ledger (ADR 0055) ──

PLAYABLE = ["py-rounding-bug", "py-volume-discount", "py-missing-test"]


def _usage(name: str, context: str) -> dict[str, int]:
    spec = yaml.safe_load((evals.TICKETS / f"{name}.yaml").read_text(encoding="utf-8"))
    return dict(spec["scripted_usage"][context])


@pytest.mark.parametrize("path", TICKETS, ids=lambda p: p.stem)
def test_every_ticket_scripts_its_usage_for_the_four_contexts(path: Path) -> None:
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    usage = spec["scripted_usage"]
    # A context alone is its `mcp` cell; `<context>/<tools>` another door's cell (S57-09).
    assert {key for key in usage if "/" not in key} == set(evals.CONTEXTS)
    assert {key.split("/")[1] for key in usage if "/" in key} <= set(evals.TOOLS) - {"mcp"}
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
        "fixtures": ["py-rounding-bug"],
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
    assert cell["median_requests"] == 3, "the round trips are the gateway's count (S57-01)"

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
            "fixtures": ["py-rounding-bug"],
        }
    )
    assert cell["context"] == "none"
    assert cell["median_tokens_in"] == _usage("py-rounding-bug", "none")["tokens_in"]


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


# ── S45-24: the fixtures are English, the former names keep resolving (ADR 0098) ──


async def test_former_fixture_name_still_runs(setup: Fixture) -> None:
    """A stored matrix that names `py-bug-arrondi` plays the ticket that replaced it."""
    assert evals.FORMER_FIXTURE_NAMES["py-bug-arrondi"] == "py-rounding-bug"  # lang: fr-ok
    cell = await evals.run_eval_cell(
        {
            "project_slug": setup.project_slug,
            "backend": "codex",
            "model": "platform/standard",
            "with_memory": True,
            "fixtures": ["py-bug-arrondi"],  # lang: fr-ok
        }
    )
    assert [(d["fixture"], d["ok"]) for d in cell["details"]] == [("py-rounding-bug", True)]
    assert cell["cases"] == 1 and cell["success_rate"] == 1.0


def test_every_former_fixture_name_resolves_to_a_ticket() -> None:
    assert len(evals.FORMER_FIXTURE_NAMES) == 6
    for former, current in evals.FORMER_FIXTURE_NAMES.items():
        assert not (evals.TICKETS / f"{former}.yaml").exists(), former
        assert (evals.TICKETS / f"{current}.yaml").is_file(), current
        assert evals._load_spec(former) == evals._load_spec(current)


def test_a_french_ticket_fixture_still_exists() -> None:
    """`py-rounding-bug-fr` keeps a French prompt on purpose: the agent answers in English (S22-03)."""
    path = evals.TICKETS / "py-rounding-bug-fr.yaml"
    assert path.is_file()
    assert "lang: fr-ok-file" in path.read_text(encoding="utf-8").splitlines()[0]
    spec = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert spec["id"] == "py-rounding-bug-fr"
    assert "moitié vers le haut" in spec["prompt"]  # lang: fr-ok
