"""Un projet neuf hérite de ce que son déploiement sait faire tourner (#245).

Le 06/10, sur le dev, le premier agent du gabarit RH n'a jamais atteint le modèle : un projet neuf
partait sur `claude-code` et aucun profil, que le dev ne sert pas — il sert opencode derrière la
passerelle de la plateforme. Le déploiement dit désormais son backend et ses profils ; un projet qui
naît sans en parler en hérite, un projet qui en parle garde les siens.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"


@pytest.fixture
def deploiement(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_DEFAULT_AGENT_BACKEND", "opencode")
    monkeypatch.setenv("CHOREGOS_ALLOWED_AGENT_BACKENDS", '["opencode", "codex"]')
    monkeypatch.setenv("CHOREGOS_DEFAULT_MODEL_PROFILES", '{"standard": "platform/standard"}')
    reset_settings_cache()
    try:
        yield
    finally:
        monkeypatch.delenv("CHOREGOS_DEFAULT_AGENT_BACKEND")
        monkeypatch.delenv("CHOREGOS_ALLOWED_AGENT_BACKENDS")
        monkeypatch.delenv("CHOREGOS_DEFAULT_MODEL_PROFILES")
        reset_settings_cache()


async def _projet(client: AsyncClient, slug: str, **config: Any) -> dict[str, Any]:
    corps = {"slug": slug, "name": slug, "config": {"slug": slug, "org": "varga", **config}}
    cree = await client.post(f"{ORG}/projects", json=corps)
    assert cree.status_code == 201, cree.text
    return dict((await client.get(f"/api/v1/projects/{cree.json()['id']}")).json()["config"])


async def test_un_projet_neuf_herite_du_backend_et_des_profils_du_deploiement(
    client: AsyncClient, admin: str, deploiement: None
) -> None:
    config = await _projet(client, "rh")
    assert config["agent"] == {"default_backend": "opencode", "allowed_backends": ["opencode", "codex"]}
    assert config["models"]["profiles"]["standard"]["litellm_model"] == "platform/standard"


async def test_un_projet_qui_en_parle_garde_les_siens(
    client: AsyncClient, admin: str, deploiement: None
) -> None:
    config = await _projet(
        client,
        "billing",
        agent={"default_backend": "claude-code", "allowed_backends": ["claude-code"]},
        models={"profiles": {"standard": "anthropic/claude-sonnet-5"}},
    )
    assert config["agent"]["default_backend"] == "claude-code"
    assert config["models"]["profiles"]["standard"]["litellm_model"] == "anthropic/claude-sonnet-5"


async def test_sans_defaut_du_deploiement_rien_ne_change(client: AsyncClient, admin: str) -> None:
    config = await _projet(client, "vieux")
    assert config["agent"]["default_backend"] == "claude-code"
    assert config["models"]["profiles"] == {}


async def test_un_backend_seul_est_aussi_le_seul_permis(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_DEFAULT_AGENT_BACKEND", "opencode")
    reset_settings_cache()
    try:
        config = await _projet(client, "seul")
    finally:
        monkeypatch.delenv("CHOREGOS_DEFAULT_AGENT_BACKEND")
        reset_settings_cache()
    assert config["agent"] == {"default_backend": "opencode", "allowed_backends": ["opencode"]}
    assert config["models"]["profiles"] == {}, "aucun profil dit : aucun profil posé"
