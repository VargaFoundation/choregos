"""Le jeton git d'un run : limité à son dépôt, une heure, `contents: write` (S22-07).

`docs/security.md` le promettait — « émis par run, portée du dépôt, 1 h, en mémoire » — et le code ne
le faisait pas : le runner clonait sans identifiant, et le bac à sable privé du locataire dev rendait
« Repository not found » à chaque run, le 08/10.
"""

from __future__ import annotations

import itertools
import time
from typing import Any

import pytest
from httpx import AsyncClient

_CLES = itertools.count(1)


async def _run_avec_depot(project: dict[str, Any], url: str, **champs: Any) -> str:
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        item = WorkItem(
            project_id=project["id"], tracker_key=f"ACME-{next(_CLES)}", title="Un correctif", state="planned"
        )
        session.add(item)
        await session.flush()
        run = Run(
            work_item_id=item.id,
            project_id=project["id"],
            stage_role="implement",
            attempt=1,
            status="running",
            stage_input={"repo": {"url": url, "base_branch": "main", "work_branch": "choregos/acme-1"}},
            **champs,
        )
        session.add(run)
        await session.flush()
        return str(run.id)


def _jeton_de_run(run_id: str) -> dict[str, str]:
    from choregos_api.config import get_settings
    from choregos_api.security import mint_run_token

    jeton = mint_run_token(
        run_id, project_slug="billing-api", work_item_key="ACME-1", ttl_minutes=5, settings=get_settings()
    )
    return {"Authorization": f"Bearer {jeton}"}


@pytest.fixture
def app_github(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Une App GitHub posée, et sa frappe de jetons interceptée : on garde ce qu'on lui a demandé."""
    from choregos_adapters.github import GitHubAppAuth, InstallationToken

    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "5242171")
    monkeypatch.setenv(
        "CHOREGOS_GITHUB_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"
    )
    demandes: list[dict[str, Any]] = []

    async def frapper(self: Any, client: Any, repo: str, **options: Any) -> InstallationToken:
        demandes.append({"repo": repo, **options})
        return InstallationToken(token="ghs_du_run", expires_at=time.time() + 3600, repositories=(repo,))

    monkeypatch.setattr(GitHubAppAuth, "token_for", frapper)
    return demandes


async def test_un_run_recoit_un_jeton_limite_a_son_depot(
    client: AsyncClient, project: dict[str, Any], app_github: list[dict[str, Any]]
) -> None:
    run_id = await _run_avec_depot(project, "https://github.com/DiametralGroup/choregos-sandbox-dev.git")
    reponse = await client.post(f"/api/v1/internal/runs/{run_id}/git-token", headers=_jeton_de_run(run_id))
    assert reponse.status_code == 200, reponse.text
    corps = reponse.json()
    assert (corps["token"], corps["repository"]) == ("ghs_du_run", "DiametralGroup/choregos-sandbox-dev")
    assert corps["expires_at"]
    from choregos_adapters.github import RUNNER_PERMISSIONS

    assert app_github == [
        {"repo": "DiametralGroup/choregos-sandbox-dev", "permissions": RUNNER_PERMISSIONS, "ttl_s": 3600}
    ]
    assert RUNNER_PERMISSIONS == {"contents": "write", "metadata": "read"}, (
        "rien de plus que cloner et pousser"
    )


async def test_sans_app_ou_hors_github_le_jeton_est_nul(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CHOREGOS_GITHUB_APP_ID", raising=False)
    run_id = await _run_avec_depot(project, "https://github.com/acme/public.git")
    corps = (
        await client.post(f"/api/v1/internal/runs/{run_id}/git-token", headers=_jeton_de_run(run_id))
    ).json()
    assert (corps["token"], corps["repository"]) == (None, "acme/public")

    ailleurs = await _run_avec_depot(project, "https://gitlab.com/acme/app.git")
    corps = (
        await client.post(f"/api/v1/internal/runs/{ailleurs}/git-token", headers=_jeton_de_run(ailleurs))
    ).json()
    assert (corps["token"], corps["repository"]) == (None, None)


async def test_un_run_fini_ou_un_inconnu_n_obtient_rien(
    client: AsyncClient, project: dict[str, Any], app_github: list[dict[str, Any]]
) -> None:
    fini = await _run_avec_depot(
        project, "https://github.com/acme/app.git", result={"status": "done", "summary": "x"}
    )
    assert (
        await client.post(f"/api/v1/internal/runs/{fini}/git-token", headers=_jeton_de_run(fini))
    ).status_code == 409
    autre = await _run_avec_depot(project, "https://github.com/acme/app.git")
    assert (await client.post(f"/api/v1/internal/runs/{autre}/git-token")).status_code == 401
    assert (
        await client.post(f"/api/v1/internal/runs/{autre}/git-token", headers=_jeton_de_run(fini))
    ).status_code in {401, 403}
    assert app_github == [], "aucun jeton frappé pour un run fini, sans jeton, ou au nom d'un autre"
