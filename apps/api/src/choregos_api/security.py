# SPDX-License-Identifier: Apache-2.0
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

from .config import DEFAULT_SESSION_SECRET, MIN_SESSION_SECRET_BYTES, Settings, get_settings

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


def public_from_private(private_pem: str) -> str:
    """La clé publique se DÉDUIT de la privée : il n'y a donc rien à tenir en double."""
    key = serialization.load_pem_private_key(private_pem.encode(), password=None)
    return (
        key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM, format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        .decode()
    )


def run_token_keys(settings: Settings | None = None) -> tuple[str, str]:
    """Les clés des jetons de run, et pourquoi la privée suffit.

    Ces jetons sont émis par l'orchestrateur et vérifiés par l'API : les deux processus
    doivent partager la MÊME paire. Exiger les deux moitiés comme avant laissait une
    troisième possibilité — n'en fournir qu'une — où chaque processus repartait sur une
    paire éphémère de son côté. Tout paraissait configuré, et l'agent recevait
    « Signature verification failed », un message qui accuse le jeton, pas le déploiement.
    """
    settings = settings or get_settings()
    if settings.run_token_private_key:
        return settings.run_token_private_key, (
            settings.run_token_public_key or public_from_private(settings.run_token_private_key)
        )
    if settings.run_token_public_key:
        # Une publique seule ne permet pas de signer : il manque l'essentiel, et le dire
        # vaut mieux que de vérifier des jetons qu'on ne saura jamais émettre.
        raise RuntimeError(
            "CHOREGOS_RUN_TOKEN_PUBLIC_KEY est fournie sans sa clé privée : "
            "l'orchestrateur ne peut pas signer les jetons de run."
        )
    # Aucune clé : paire éphémère, valable tant qu'un SEUL processus émet et vérifie
    # (développement, tests). En cluster, l'API et l'orchestrateur sont deux processus.
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


# ───────────────────────────── relay tokens ─────────────────────────────

#: Not the run token's audience: neither token is accepted for the other's routes (ADR 0091 §3).
RELAY_TOKEN_AUDIENCE = "choregos-relay"
RELAY_TOKEN_MAX_DAYS = 400


def mint_relay_token(org_slug: str, *, days: int, settings: Settings | None = None) -> str:
    """The outbound-proxy relay's credential for ONE organisation: ES256 with the run-token key,
    `aud=choregos-relay`, `sub` = the organisation's slug, always expiring (ADR 0091 §3)."""
    if not org_slug:
        raise ValueError("a relay token names one organisation")
    if not 1 <= days <= RELAY_TOKEN_MAX_DAYS:
        raise ValueError(f"a relay token lives between 1 and {RELAY_TOKEN_MAX_DAYS} days")
    settings = settings or get_settings()
    private_key, _ = run_token_keys(settings)
    now = int(time.time())
    payload = {
        "iss": settings.run_token_issuer,
        "aud": RELAY_TOKEN_AUDIENCE,
        "sub": org_slug,
        "iat": now,
        "exp": now + days * 86400,
    }
    return jwt.encode(payload, private_key, algorithm=RUN_TOKEN_ALGORITHM)


def verify_relay_token(token: str, settings: Settings | None = None) -> str:
    """The organisation a relay token is for. Raises `jwt.PyJWTError` when it is invalid, expired,
    has no `exp`, or is for another audience (a run token is refused here)."""
    settings = settings or get_settings()
    _, public_key = run_token_keys(settings)
    payload: dict[str, Any] = jwt.decode(
        token,
        public_key,
        algorithms=[RUN_TOKEN_ALGORITHM],
        audience=RELAY_TOKEN_AUDIENCE,
        issuer=settings.run_token_issuer,
        options={"require": ["exp", "sub", "aud", "iss"]},
    )
    return str(payload["sub"])


# ───────────────────────────── egress tokens ─────────────────────────────

#: Not the run token's audience: what the egress proxy reads a run's network with (ADR 0115). The
#: agent inherits the proxy variables that carry it, so it opens those two routes and no other.
EGRESS_TOKEN_AUDIENCE = "choregos-egress"


def mint_egress_token(claims: RunClaims, settings: Settings | None = None) -> str:
    """The egress token of the run `claims` names: same run, same expiry, `aud=choregos-egress`."""
    settings = settings or get_settings()
    private_key, _ = run_token_keys(settings)
    payload = {
        "iss": settings.run_token_issuer,
        "aud": EGRESS_TOKEN_AUDIENCE,
        "sub": claims.run_id,
        "iat": int(time.time()),
        "exp": claims.expires_at,
        "project": claims.project_slug,
        "work_item": claims.work_item_key,
    }
    return jwt.encode(payload, private_key, algorithm=RUN_TOKEN_ALGORITHM)


def verify_egress_token(token: str, settings: Settings | None = None) -> RunClaims:
    """The run an egress token is for. Raises `jwt.PyJWTError` when it is invalid, expired, or for
    another audience (a run token is refused here, and an egress token by `verify_run_token`)."""
    settings = settings or get_settings()
    _, public_key = run_token_keys(settings)
    payload: dict[str, Any] = jwt.decode(
        token,
        public_key,
        algorithms=[RUN_TOKEN_ALGORITHM],
        audience=EGRESS_TOKEN_AUDIENCE,
        issuer=settings.run_token_issuer,
        options={"require": ["exp", "sub", "aud", "iss"]},
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

#: What a value signed with the session secret is for. The tag covers it, so a value signed for one
#: purpose (an OIDC state) never reads as another (a session cookie).
SESSION_PURPOSE = "session"
#: Length of the tag the previous release put on a session cookie: 128 bits, hex, with no purpose.
LEGACY_TAG_LENGTH = 32
#: Cookies signed before this release carry a 128-bit tag without a purpose. They are accepted for
#: `session` only, for one release, so that the upgrade does not sign everyone out. Removal is
#: tracked in https://github.com/VargaFoundation/choregos/issues/1040.
ACCEPT_LEGACY_SESSION_TAGS = True


def check_session_secret(settings: Settings) -> None:
    """Refuse to start on a session secret that cannot sign safely.

    Called by the API at start only. The orchestrator, the migrations Job and the trace-drift job
    build `Settings` without the secret and never sign sessions, so this is not a `Settings`
    validator: it would crash every one of them.
    """
    secret = settings.session_secret
    if not secret.strip():
        raise RuntimeError("CHOREGOS_SESSION_SECRET is empty: an empty HMAC key lets anyone forge a session")
    if settings.deployed and (
        secret == DEFAULT_SESSION_SECRET or len(secret.encode()) < MIN_SESSION_SECRET_BYTES
    ):
        raise RuntimeError(
            "CHOREGOS_SESSION_SECRET is the published default or shorter than "
            f"{MIN_SESSION_SECRET_BYTES} bytes on a deployed instance (staging/prod, or served over "
            "https): anyone can forge a session. Set a random value, for example "
            "`openssl rand -base64 48`"
        )
    previous = settings.session_secret_previous
    if settings.deployed and previous and len(previous.encode()) < MIN_SESSION_SECRET_BYTES:
        raise RuntimeError(
            f"CHOREGOS_SESSION_SECRET_PREVIOUS is shorter than {MIN_SESSION_SECRET_BYTES} bytes on a "
            "deployed instance: a verification key that short still lets anyone forge a session while "
            "it is kept. Unset it (the sessions it signed then end), or check the old value was copied whole"
        )


def _tag(key: str, purpose: str, body: str) -> str:
    """The full HMAC-SHA256 tag (64 hex characters) of `body`, bound to `purpose`."""
    message = f"choregos:{purpose}" + chr(0) + body
    return hmac.new(key.encode(), message.encode(), hashlib.sha256).hexdigest()


def _legacy_tag(key: str, body: str) -> str:
    """The tag the previous release wrote: 128 bits, no purpose."""
    return hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()[:LEGACY_TAG_LENGTH]


def _same(left: str, right: str) -> bool:
    """Constant-time comparison; as bytes, because `compare_digest` refuses a non-ASCII `str`."""
    return hmac.compare_digest(left.encode(errors="replace"), right.encode(errors="replace"))


def sign_session(
    payload: dict[str, Any], settings: Settings | None = None, *, purpose: str = SESSION_PURPOSE
) -> str:
    settings = settings or get_settings()
    body = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    return f"{body}.{_tag(settings.session_secret, purpose, body)}"


def read_session(
    cookie: str, settings: Settings | None = None, *, purpose: str = SESSION_PURPOSE
) -> dict[str, Any] | None:
    """The payload of a value signed for `purpose` with the current or the previous key, if unexpired."""
    settings = settings or get_settings()
    if not cookie or "." not in cookie:
        return None
    body, signature = cookie.rsplit(".", 1)
    keys = [settings.session_secret]
    # Never an empty key: an empty HMAC key is one anyone can sign with.
    if settings.session_secret_previous:
        keys.append(settings.session_secret_previous)
    legacy = purpose == SESSION_PURPOSE and ACCEPT_LEGACY_SESSION_TAGS and len(signature) == LEGACY_TAG_LENGTH
    if not any(
        _same(signature, _tag(key, purpose, body)) or (legacy and _same(signature, _legacy_tag(key, body)))
        for key in keys
    ):
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
