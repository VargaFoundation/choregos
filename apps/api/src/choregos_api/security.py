"""Sécurité : jetons de run (ES256), jetons d'API, sessions signées, vérification de webhooks."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from .config import Settings, get_settings

RUN_TOKEN_ALGORITHM = "ES256"


@dataclass(slots=True, frozen=True)
class RunClaims:
    """Portée d'un jeton de run : un run, rien d'autre."""

    run_id: str
    project_slug: str
    work_item_key: str
    expires_at: int

    @property
    def expired(self) -> bool:
        return time.time() > self.expires_at


@lru_cache(maxsize=1)
def _dev_keypair() -> tuple[str, str]:
    """Paire ES256 éphémère pour le développement (jamais en production)."""
    key = ec.generate_private_key(ec.SECP256R1())
    private_pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ).decode()
    public_pem = (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )
    return private_pem, public_pem


def run_token_keys(settings: Settings | None = None) -> tuple[str, str]:
    settings = settings or get_settings()
    if settings.run_token_private_key and settings.run_token_public_key:
        return settings.run_token_private_key, settings.run_token_public_key
    return _dev_keypair()


def mint_run_token(
    run_id: str,
    *,
    project_slug: str,
    work_item_key: str,
    ttl_minutes: int,
    settings: Settings | None = None,
) -> str:
    """JWT ES256 `aud=internal`, `sub=run_id`, TTL = max_minutes + 15 (§1.10)."""
    settings = settings or get_settings()
    private_key, _ = run_token_keys(settings)
    now = int(time.time())
    payload = {
        "iss": settings.run_token_issuer,
        "aud": settings.run_token_audience,
        "sub": run_id,
        "iat": now,
        "exp": now + int(ttl_minutes * 60),
        "project": project_slug,
        "work_item": work_item_key,
    }
    return jwt.encode(payload, private_key, algorithm=RUN_TOKEN_ALGORITHM)


def verify_run_token(token: str, settings: Settings | None = None) -> RunClaims:
    """Vérifie un jeton de run. Lève `jwt.PyJWTError` si invalide, expiré ou d'un autre run."""
    settings = settings or get_settings()
    _, public_key = run_token_keys(settings)
    payload: dict[str, Any] = jwt.decode(
        token,
        public_key,
        algorithms=[RUN_TOKEN_ALGORITHM],
        audience=settings.run_token_audience,
        issuer=settings.run_token_issuer,
    )
    return RunClaims(
        run_id=str(payload["sub"]),
        project_slug=str(payload.get("project", "")),
        work_item_key=str(payload.get("work_item", "")),
        expires_at=int(payload["exp"]),
    )


# ───────────────────────────── jetons d'API ─────────────────────────────


def generate_api_token() -> tuple[str, str]:
    """Rend (jeton en clair, empreinte stockée). Le clair n'est jamais persisté."""
    raw = "chg_" + secrets.token_urlsafe(32)
    return raw, hash_api_token(raw)


def hash_api_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# ───────────────────────────── sessions ─────────────────────────────


def sign_session(payload: dict[str, Any], settings: Settings | None = None) -> str:
    settings = settings or get_settings()
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    signature = hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{signature}"


def read_session(cookie: str, settings: Settings | None = None) -> dict[str, Any] | None:
    settings = settings or get_settings()
    if not cookie or "." not in cookie:
        return None
    body, signature = cookie.rsplit(".", 1)
    expected = hmac.new(settings.session_secret.encode(), body.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(signature, expected):
        return None
    padded = body + "=" * (-len(body) % 4)
    try:
        payload: dict[str, Any] = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
    except (ValueError, json.JSONDecodeError):
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


# ───────────────────────────── webhooks ─────────────────────────────


def verify_github_signature(secret: str, body: bytes, signature_header: str) -> bool:
    """HMAC SHA-256 comparé en temps constant (§1.10)."""
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


def verify_shared_secret(secret: str, provided: str) -> bool:
    return bool(secret) and hmac.compare_digest(secret, provided or "")


def body_digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()[:40]
