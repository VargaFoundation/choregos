"""Run tokens: a single half of a key is enough to configure the two processes."""

from __future__ import annotations

import pytest
from choregos_api.config import Settings
from choregos_api.security import mint_run_token, public_from_private, run_token_keys, verify_run_token


def _settings(**kwargs: object) -> Settings:
    return Settings(_env_file=None, **kwargs)  # type: ignore[arg-type]


def test_the_public_key_is_derived_from_the_private_one() -> None:
    """Otherwise a deployment has to keep both halves in agreement, and when it sets only one,
    each process falls back to an ephemeral pair: the agent then receives
    "Signature verification failed", which accuses the token and not the configuration."""
    private, public = run_token_keys(_settings())
    settings = _settings(run_token_private_key=private)

    signing, derived = run_token_keys(settings)
    assert signing == private
    assert derived == public == public_from_private(private)

    token = mint_run_token("r-1", project_slug="demo", work_item_key="D-1", ttl_minutes=5, settings=settings)
    assert verify_run_token(token, settings).run_id == "r-1"


def test_a_public_key_alone_is_refused() -> None:
    _, public = run_token_keys(_settings())
    with pytest.raises(RuntimeError, match="sans sa clé privée"):  # lang: fr-ok pinned by the source message
        run_token_keys(_settings(run_token_public_key=public))


def test_an_expired_token_or_one_from_another_public_is_refused() -> None:
    """The TTL, the audience and the issuer are verified: nothing tested it (state of play of 2026-09-24)."""
    import jwt

    settings = _settings()
    expired = mint_run_token(
        "r-1", project_slug="demo", work_item_key="D-1", ttl_minutes=0, settings=settings
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        verify_run_token(expired, settings)

    valid = mint_run_token("r-1", project_slug="demo", work_item_key="D-1", ttl_minutes=5, settings=settings)
    private, _ = run_token_keys(settings)
    with pytest.raises(jwt.InvalidAudienceError):
        verify_run_token(valid, _settings(run_token_private_key=private, run_token_audience="elsewhere"))
    with pytest.raises(jwt.InvalidIssuerError):
        verify_run_token(valid, _settings(run_token_private_key=private, run_token_issuer="other-api"))
    # another key pair: the signature is worth nothing
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    other = (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        .decode()
    )
    with pytest.raises(jwt.InvalidSignatureError):
        verify_run_token(valid, _settings(run_token_private_key=other))


def test_the_token_is_only_valid_for_its_run() -> None:
    settings = _settings()
    token = mint_run_token("r-1", project_slug="demo", work_item_key="D-1", ttl_minutes=5, settings=settings)
    claims = verify_run_token(token, settings)
    assert (claims.run_id, claims.project_slug, claims.work_item_key) == ("r-1", "demo", "D-1")
    assert claims.run_id != "r-2"
