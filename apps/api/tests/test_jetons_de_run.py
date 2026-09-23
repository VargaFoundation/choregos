"""Les jetons de run : une seule moitié de clé suffit à configurer les deux processus."""

from __future__ import annotations

import pytest
from choregos_api.config import Settings
from choregos_api.security import mint_run_token, public_from_private, run_token_keys, verify_run_token


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_la_cle_publique_se_deduit_de_la_privee() -> None:
    """Sinon un déploiement doit tenir les deux moitiés en accord — et quand il n'en pose
    qu'une, chaque processus repart sur une paire éphémère : l'agent reçoit alors
    « Signature verification failed », qui accuse le jeton et non la configuration."""
    privee, publique = run_token_keys(_settings())
    reglages = _settings(run_token_private_key=privee)

    signee, deduite = run_token_keys(reglages)
    assert signee == privee
    assert deduite == publique == public_from_private(privee)

    jeton = mint_run_token("r-1", project_slug="demo", work_item_key="D-1", ttl_minutes=5, settings=reglages)
    assert verify_run_token(jeton, reglages).run_id == "r-1"


def test_une_cle_publique_seule_est_refusee() -> None:
    _, publique = run_token_keys(_settings())
    with pytest.raises(RuntimeError, match="sans sa clé privée"):
        run_token_keys(_settings(run_token_public_key=publique))
