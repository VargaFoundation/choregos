"""The CLI and a project's machine accounts (ADR 0131)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
BASE = f"{API}/api/v1/projects/acme:billing-api/machine-accounts"


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("CHOREGOS_API_URL", API)
    monkeypatch.setenv("CHOREGOS_TOKEN", "chg_test")
    monkeypatch.setenv("CHOREGOS_ORG", "acme")
    monkeypatch.setenv("COLUMNS", "200")


def _app():  # type: ignore[no-untyped-def]
    from choregos_cli.main import app

    return app


@respx.mock
def test_create_then_issue_a_token_shown_once() -> None:
    created = respx.post(BASE).mock(
        return_value=Response(
            201,
            json={"slug": "partner-app", "display_name": "partner-app", "writes_per_day": 100, "tokens": []},
        )
    )
    token = respx.post(f"{BASE}/partner-app/tokens").mock(
        return_value=Response(201, json={"name": "main", "expires_at": "2027-02-07", "token": "chg_once"})
    )
    runner = CliRunner()
    made = runner.invoke(
        _app(), ["machines", "create", "acme:billing-api", "partner-app", "--writes-per-day", "100"]
    )
    assert made.exit_code == 0, made.output
    assert json.loads(created.calls[0].request.content) == {"slug": "partner-app", "writes_per_day": 100}
    issued = runner.invoke(_app(), ["machines", "token", "acme:billing-api", "partner-app", "--name", "main"])
    assert issued.exit_code == 0, issued.output
    assert "chg_once" in issued.output
    assert json.loads(token.calls[0].request.content) == {"name": "main", "expires_in_days": 90}


@respx.mock
def test_list_shows_tokens_without_their_value() -> None:
    respx.get(BASE).mock(
        return_value=Response(
            200,
            json=[
                {
                    "slug": "partner-app",
                    "display_name": "Partner",
                    "writes_per_day": 500,
                    "tokens": [{"id": "t1", "name": "main", "expires_at": "2027-02-07T00:00:00Z"}],
                }
            ],
        )
    )
    shown = CliRunner().invoke(_app(), ["machines", "list", "acme:billing-api"])
    assert shown.exit_code == 0, shown.output
    assert "partner-app" in shown.output and "main" in shown.output


@respx.mock
def test_revoke_and_remove() -> None:
    revoke = respx.delete(f"{BASE}/partner-app/tokens/t1").mock(return_value=Response(204))
    remove = respx.delete(f"{BASE}/partner-app").mock(return_value=Response(204))
    runner = CliRunner()
    assert (
        runner.invoke(_app(), ["machines", "revoke", "acme:billing-api", "partner-app", "t1"]).exit_code == 0
    )
    assert runner.invoke(_app(), ["machines", "remove", "acme:billing-api", "partner-app"]).exit_code == 0
    assert revoke.called and remove.called
