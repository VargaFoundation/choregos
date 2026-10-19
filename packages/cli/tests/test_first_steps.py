"""The CLI knows the path of a new installation — against a simulated API.

No test covered 30 commands (review of 2026-09-24). These check what a newcomer types first: sign in,
create a project without a repository, file a request, mint a token.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"


@pytest.fixture
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


@respx.mock
def test_a_project_without_a_repository_then_a_request(profile: Path) -> None:
    respx.post(f"{API}/api/v1/orgs/acme/projects").mock(
        return_value=Response(201, json={"id": "p-1", "slug": "staffing", "name": "Staffing"})
    )
    tracker = respx.put(f"{API}/api/v1/projects/p-1/connectors/tracker").mock(
        return_value=Response(200, json={})
    )
    result = _run("projects", "create", "staffing", "--name", "Staffing")
    assert result.exit_code == 0, result.output
    assert "staffing created" in result.output
    sent = json.loads(respx.calls[0].request.content)
    assert "repo" not in sent["config"], "without --repo, no invented repository"
    assert tracker.called and json.loads(tracker.calls[0].request.content)["type"] == "internal"

    respx.post(f"{API}/api/v1/projects/staffing/work-items").mock(
        return_value=Response(
            201, json={"tracker_key": "STAFFING-1", "state": "request", "temporal_wf_id": "wi-1"}
        )
    )
    result = _run("items", "create", "staffing", "--title", "Data project manager", "--size", "M")
    assert result.exit_code == 0, result.output
    assert "STAFFING-1 created" in result.output
    body = json.loads(respx.calls[-1].request.content)
    assert body == {"title": "Data project manager", "body": "", "size": "M", "start": True}


@respx.mock
def test_tracker_is_written_with_a_repository_too(profile: Path) -> None:
    """#704: with `--repo`, `--tracker internal` was dropped and the project had no tracker."""
    respx.post(f"{API}/api/v1/orgs/acme/projects").mock(
        return_value=Response(201, json={"id": "p-2", "slug": "evals", "name": "evals"})
    )
    tracker = respx.put(f"{API}/api/v1/projects/p-2/connectors/tracker").mock(
        return_value=Response(200, json={})
    )
    repo = "https://github.com/acme/sandbox"
    result = _run("projects", "create", "evals", "--repo", repo, "--tracker", "internal")
    assert result.exit_code == 0, result.output
    assert json.loads(respx.calls[0].request.content)["config"]["repo"]["url"] == repo
    assert tracker.called and json.loads(tracker.calls[0].request.content)["type"] == "internal"


@respx.mock
def test_with_a_repository_and_no_tracker_option_no_tracker_is_imposed(profile: Path) -> None:
    """The template, or a later connector call, says which tracker a repository uses."""
    respx.post(f"{API}/api/v1/orgs/acme/projects").mock(
        return_value=Response(201, json={"id": "p-3", "slug": "billing", "name": "billing"})
    )
    tracker = respx.put(f"{API}/api/v1/projects/p-3/connectors/tracker").mock(
        return_value=Response(200, json={})
    )
    result = _run("projects", "create", "billing", "--repo", "https://github.com/acme/billing")
    assert result.exit_code == 0, result.output
    assert not tracker.called


@respx.mock
def test_a_token_is_minted_and_shown_only_once(profile: Path) -> None:
    respx.post(f"{API}/api/v1/me/tokens").mock(
        return_value=Response(
            201, json={"id": "t-1", "name": "ci", "expires_at": "2027-01-01T00:00:00Z", "token": "chg_secret"}
        )
    )
    result = _run("tokens", "create", "--name", "ci")
    assert result.exit_code == 0, result.output
    assert "chg_secret" in result.output
    respx.get(f"{API}/api/v1/me/tokens").mock(
        return_value=Response(
            200, json=[{"id": "t-1", "name": "ci", "created_at": "2026-09-24", "expires_at": None}]
        )
    )
    result = _run("tokens", "list")
    assert result.exit_code == 0 and "chg_secret" not in result.output


@respx.mock
def test_an_api_error_reads_clearly(profile: Path) -> None:
    respx.post(f"{API}/api/v1/orgs").mock(
        return_value=Response(403, json={"detail": "creating an organisation needs the org_admin role"})
    )
    result = _run("orgs", "create", "pirate")
    assert result.exit_code != 0
    assert "org_admin" in result.output
