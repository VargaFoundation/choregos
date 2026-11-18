# SPDX-License-Identifier: Apache-2.0
"""Every amount in the organisation's currency (S67-25, ADR 0187 §1).

The API is faked with respx; every payload is validated against `openapi.yaml` (`contract`). The API states
no exchange rate: an amount it gives in one currency only is printed with its own symbol, never relabelled.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
V1 = f"{API}/api/v1"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tranibor_cli import client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_TOKEN", "chg_test")
    monkeypatch.setenv("CHOREGOS_ORG", "acme")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("NO_COLOR", raising=False)


class Platform:
    def __init__(self, router: respx.Router, contract: Any) -> None:
        self.router, self.contract = router, contract

    def org(self, currency: str | None, status: int = 200) -> respx.Route:
        body: Any = {"title": "Unauthorized"}
        if status == 200:
            org = {"slug": "acme", "name": "Acme", "aliases": ["old-acme"]}
            if currency:
                org["currency"] = currency
            body = [self.contract("Org", org)]
        self.orgs = self.router.get(f"{V1}/orgs").mock(return_value=Response(status, json=body))
        return self.orgs

    def serve(self, path: str, schema: str, body: Any) -> None:
        self.router.get(f"{V1}{path}").mock(return_value=Response(200, json=self.contract(schema, body)))


@pytest.fixture
def platform(contract: Any) -> Iterator[Platform]:
    with respx.mock(assert_all_called=False) as router:
        yield Platform(router, contract)


def _run(*words: str) -> Any:
    from tranibor_cli.main import app

    result = CliRunner().invoke(app, list(words))
    assert result.exit_code == 0, result.output
    return " ".join(ANSI.sub("", result.output).split())


def _project(**stats: Any) -> dict[str, Any]:
    return {
        "id": "p1",
        "slug": "billing",
        "org": "acme",
        "name": "Billing",
        "status": "active",
        "config": {},
        "stats": stats,
    }


def _item(**totals: Any) -> dict[str, Any]:
    return {
        "id": "w1",
        "project_slug": "billing",
        "tracker_key": "BIL-1",
        "title": "Fix",
        "state": "open",
        "totals": totals,
    }


def _serve_projects(platform: Platform, **stats: Any) -> None:
    # `Project.config` refers to a schema file the validator does not load: the stats are the part checked.
    platform.contract("ProjectStats", stats)
    body = {"items": [_project(**stats)], "meta": {"has_more": False}}
    platform.router.get(f"{V1}/orgs/acme/projects").mock(return_value=Response(200, json=body))


def _serve_items(platform: Platform, **totals: Any) -> None:
    platform.serve(
        "/projects/billing/work-items",
        "WorkItemPage",
        {"items": [_item(**totals)], "meta": {"has_more": False}},
    )


# ───────────────────────────── the organisation's currency ─────────────────────────────


def test_a_euro_organisation_reads_euros_on_every_list(platform: Platform) -> None:
    platform.org("EUR")
    _serve_projects(platform, cost_month_eur=12.5)
    _serve_items(platform, cost_usd=3.0, cost_eur=2.5)
    said = _run("projects", "list")
    assert "€12.50" in said and "$" not in said
    said = _run("items", "list", "billing")
    assert "€2.50" in said and "$" not in said


def test_a_dollar_organisation_reads_dollars_where_the_api_gives_them(platform: Platform) -> None:
    platform.org("USD")
    _serve_items(platform, cost_usd=3.0, cost_eur=2.5)
    said = _run("items", "list", "billing")
    assert "$3.00" in said and "€" not in said


def test_the_work_item_detail_and_the_runs_follow_the_currency(platform: Platform) -> None:
    platform.org("USD")
    platform.serve("/work-items/w1", "WorkItem", _item(cost_usd=3.0, cost_eur=2.5, runs=2))
    platform.router.get(f"{V1}/work-items/w1/timeline").mock(return_value=Response(200, json=[]))
    assert "cost: $3.00 (2 runs)" in _run("items", "show", "w1")
    platform.org("EUR")
    from tranibor_cli import money

    money.forget()
    assert "cost: €2.50 (2 runs)" in _run("items", "show", "w1")


def test_an_amount_given_only_in_the_other_currency_keeps_its_own_symbol(platform: Platform) -> None:
    """A dollar organisation, a project's month served in euros only: `€`, never relabelled `$`."""
    platform.org("USD")
    _serve_projects(platform, cost_month_eur=12.5)
    said = _run("projects", "list")
    assert "€12.50" in said and "$12.50" not in said
    """A euro organisation, a run's spend served in dollars only: `$`, never `€`."""
    platform.org("EUR")
    platform.router.get(f"{V1}/work-items/w1/runs").mock(
        return_value=Response(
            200,
            json=[
                platform.contract(
                    "Run", {"id": "run-1", "status": "succeeded", "stage_role": "coder", "cost_usd": 0.84}
                )
            ],
        )
    )
    said = _run("runs", "list", "w1")
    assert "$0.84" in said and "€0.84" not in said


def test_an_unknown_spend_is_a_dash_never_zero(platform: Platform) -> None:
    platform.org("EUR")
    _serve_projects(platform)  # no stats: the API has nothing to say
    said = _run("projects", "list")
    assert "—" in said and "0.00" not in said


def test_the_currency_is_read_once_per_command(platform: Platform) -> None:
    route = platform.org("EUR")
    platform.serve("/work-items/w1", "WorkItem", _item(cost_usd=3.0, cost_eur=2.5))
    platform.router.get(f"{V1}/work-items/w1/timeline").mock(return_value=Response(200, json=[]))
    _run("items", "show", "w1")
    assert route.call_count == 1
    _serve_items(platform, cost_usd=3.0, cost_eur=2.5)
    _run("items", "list", "billing")
    assert route.call_count == 1  # a second command of the same process: still no second read
    from tranibor_cli import money
    from tranibor_cli.client import Client

    api = Client()
    for _ in range(3):
        assert money.currency_of(api, "acme") == "EUR"
    assert route.call_count == 1  # the process remembers it: three more asks, no more reads


def test_an_organisation_the_api_will_not_describe_prints_every_amount_in_its_own_symbol(
    platform: Platform,
) -> None:
    platform.org(None, status=403)
    _serve_items(platform, cost_usd=3.0, cost_eur=2.5)
    said = _run("items", "list", "billing")
    assert "€2.50" in said and "$" not in said


def test_the_organisation_is_found_by_an_old_slug(platform: Platform) -> None:
    from tranibor_cli import money
    from tranibor_cli.client import Client

    platform.org("USD")
    assert money.currency_of(Client(), "old-acme") == "USD"


# ───────────────────────────── activity and ask ─────────────────────────────


def _activity(platform: Platform, *, usd: float | None, eur: float | None) -> Any:
    platform.serve("/orgs/acme/activity", "ActivityList", {"in_flight": [], "finished": []})
    total = {"cost_usd": usd, "cost_eur": eur}
    platform.router.get(f"{V1}/orgs/acme/costs").mock(
        return_value=Response(
            200, json=platform.contract("CostReport", {"group_by": "day", "rows": [], "total": total})
        )
    )
    return _run("activity")


def test_the_activity_status_line_is_in_the_organisation_currency(platform: Platform) -> None:
    platform.org("EUR")
    said = _activity(platform, usd=1.5, eur=1.25)
    assert "€1.25 today" in said and "$" not in said
    from tranibor_cli import money

    money.forget()
    platform.org("USD")
    said = _activity(platform, usd=1.5, eur=1.25)
    assert "$1.50 today" in said and "€" not in said


def test_the_status_line_takes_a_bare_number_as_dollars() -> None:
    from tranibor_cli import activity

    board = activity.Board.of({"in_flight": [], "finished": []})
    assert "$1.24 today" in activity.status_line(board, "acme", 1.2399).plain
    assert "— today" in activity.status_line(board, "acme", None).plain


def test_an_unread_amount_in_one_currency_never_takes_the_others_symbol() -> None:
    from tranibor_cli.money import Amount

    assert Amount(usd=2.0).text("EUR") == "$2.00"
    assert Amount(eur=2.0).text("USD") == "€2.00"
    assert Amount(usd=2.0, eur=1.8).text("EUR") == "€1.80"
    assert Amount(usd=2.0, eur=1.8).text("USD") == "$2.00"
    assert Amount().text("EUR") == "—"


def test_the_ask_cost_line_prints_the_gateways_dollars_with_their_symbol(capsys: Any) -> None:
    from rich.console import Console
    from tranibor_cli import ask

    class Api:
        def get(self, path: str) -> dict[str, Any]:
            return {"cost_usd": 0.0125}

    err = Console(stderr=True, force_terminal=False, width=200)
    ask._footer(err, Api(), "c1", {"status": "completed", "usage": {}}, [])  # type: ignore[arg-type]
    assert "$0.0125 spent in this conversation" in capsys.readouterr().err
