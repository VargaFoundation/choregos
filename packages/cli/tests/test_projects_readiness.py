"""`choregos projects readiness <slug>`: the API's list of what a project lacks to run (S40-06)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"api_url": API, "token": "chg_x", "org": "acme"}))
    monkeypatch.setenv("CHOREGOS_CONFIG", str(path))
    import choregos_cli.client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", path)
    return path


def _run(*args: str) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, list(args))


def _report(*items: dict[str, Any]) -> dict[str, Any]:
    states = [i["state"] for i in items]
    return {
        "ready": "todo" not in states,
        "summary": {s: states.count(s) for s in ("done", "check", "todo")},
        "items": list(items),
    }


def _item(id_: str, state: str, title: str, by: str = "project_owner") -> dict[str, Any]:
    return {
        "id": id_,
        "section": "connections",
        "title": title,
        "state": state,
        "detail": f"{title} detail",
        "fix": {"href": "/p/dev/settings", "by": by},
    }


@respx.mock
def test_a_project_that_lacks_something_prints_it_and_exits_one() -> None:
    respx.get(f"{API}/api/v1/projects/dev/readiness").mock(
        return_value=Response(
            200,
            json=_report(
                _item("a", "done", "Tracker ok"),
                _item("b", "todo", "CD missing"),
                _item("c", "not_needed", "Hidden one"),
            ),
        )
    )
    result = _run("projects", "readiness", "dev")
    assert result.exit_code == 1, result.output
    assert "CD missing" in result.output and "project_owner" in result.output
    assert "Hidden one" not in result.output, "an item the project does not need is not shown"
    assert "1 done, 0 to check, 1 to do" in result.output


@respx.mock
def test_a_ready_project_exits_zero() -> None:
    respx.get(f"{API}/api/v1/projects/dev/readiness").mock(
        return_value=Response(200, json=_report(_item("a", "done", "Tracker ok")))
    )
    result = _run("projects", "readiness", "dev")
    assert result.exit_code == 0, result.output
