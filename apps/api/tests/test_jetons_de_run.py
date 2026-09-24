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


def test_un_jeton_expire_ou_d_un_autre_public_est_refuse() -> None:
    """Le TTL, l'audience et l'émetteur sont vérifiés — rien ne le testait (état des lieux du 2026-09-24)."""
    import jwt

    reglages = _settings()
    expire = mint_run_token("r-1", project_slug="demo", work_item_key="D-1", ttl_minutes=0, settings=reglages)
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_run_token(expire, reglages)

    valide = mint_run_token("r-1", project_slug="demo", work_item_key="D-1", ttl_minutes=5, settings=reglages)
    privee, _ = run_token_keys(reglages)
    with pytest.raises(jwt.InvalidAudienceError):
        verify_run_token(valide, _settings(run_token_private_key=privee, run_token_audience="ailleurs"))
    with pytest.raises(jwt.InvalidIssuerError):
        verify_run_token(valide, _settings(run_token_private_key=privee, run_token_issuer="autre-api"))
    # une autre paire de clés : la signature ne vaut rien
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    autre = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        .decode()
    )
    with pytest.raises(jwt.InvalidSignatureError):
        verify_run_token(valide, _settings(run_token_private_key=autre))


def test_le_jeton_ne_vaut_que_pour_son_run() -> None:
    reglages = _settings()
    jeton = mint_run_token("r-1", project_slug="demo", work_item_key="D-1", ttl_minutes=5, settings=reglages)
    claims = verify_run_token(jeton, reglages)
    assert (claims.run_id, claims.project_slug, claims.work_item_key) == ("r-1", "demo", "D-1")
    assert claims.run_id != "r-2"
