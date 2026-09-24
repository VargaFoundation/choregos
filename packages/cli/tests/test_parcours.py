"""La CLI sait faire le parcours d'une installation neuve — contre une API simulée.

Zéro test couvrait 30 commandes (état des lieux du 2026-09-24). Ceux-ci vérifient ce
qu'un nouvel arrivant tape en premier : se connecter, créer un projet sans dépôt, poser
une demande, frapper un jeton.
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
def profil(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    chemin = tmp_path / "config.json"
    chemin.write_text(json.dumps({"api_url": API, "token": "chg_x", "org": "acme"}))
    monkeypatch.setenv("CHOREGOS_CONFIG", str(chemin))
    import choregos_cli.client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", chemin)
    return chemin


def _run(*args: str) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, list(args))


@respx.mock
def test_un_projet_sans_depot_puis_une_demande(profil: Path) -> None:
    respx.post(f"{API}/api/v1/orgs/acme/projects").mock(
        return_value=Response(201, json={"id": "p-1", "slug": "staffing", "name": "Staffing"})
    )
    tracker = respx.put(f"{API}/api/v1/projects/p-1/connectors/tracker").mock(
        return_value=Response(200, json={})
    )
    result = _run("projects", "create", "staffing", "--name", "Staffing")
    assert result.exit_code == 0, result.output
    assert "staffing créé" in result.output
    envoye = json.loads(respx.calls[0].request.content)
    assert "repo" not in envoye["config"], "sans --repo, pas de dépôt inventé"
    assert tracker.called and json.loads(tracker.calls[0].request.content)["type"] == "internal"

    respx.post(f"{API}/api/v1/projects/staffing/work-items").mock(
        return_value=Response(
            201, json={"tracker_key": "STAFFING-1", "state": "demande", "temporal_wf_id": "wi-1"}
        )
    )
    result = _run("items", "create", "staffing", "--title", "Chef de projet data", "--size", "M")
    assert result.exit_code == 0, result.output
    assert "STAFFING-1 créé" in result.output
    corps = json.loads(respx.calls[-1].request.content)
    assert corps == {"title": "Chef de projet data", "body": "", "size": "M", "start": True}


@respx.mock
def test_un_jeton_se_frappe_et_ne_s_affiche_qu_une_fois(profil: Path) -> None:
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
def test_une_erreur_de_l_api_se_lit(profil: Path) -> None:
    respx.post(f"{API}/api/v1/orgs").mock(
        return_value=Response(403, json={"detail": "créer une organisation demande le rôle org_admin"})
    )
    result = _run("orgs", "create", "pirate")
    assert result.exit_code != 0
    assert "org_admin" in result.output
