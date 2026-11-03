# SPDX-License-Identifier: Apache-2.0
"""Secrets and remote credentials."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base, Json, PkMixin, TimestampMixin


class OrgSecret(Base, PkMixin, TimestampMixin):
    """A secret value typed in the console, sealed at rest (ADR 0058).

    Envelope encryption: the value is encrypted with a random data key (AES-256-GCM); the data key
    is wrapped under a key-encryption key (KEK) the DEPLOYMENT holds (`CHOREGOS_SECRETS_KEKS`), never
    the database. The additional authenticated data binds a row to its organisation, name and
    version, so it cannot be moved. No plaintext, no hash, no fingerprint: the hash of a
    low-entropy secret leaks it."""

    __tablename__ = "org_secrets"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_org_secrets_org_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    #: `connector/<name>/<field>` or `project/<slug>/<kind>/<field>`
    name: Mapped[str] = mapped_column(String(200))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary)
    dek_nonce: Mapped[bytes] = mapped_column(LargeBinary)
    kek_id: Mapped[str] = mapped_column(String(32))
    algorithm: Mapped[str] = mapped_column(String(16), default="A256GCM")
    #: +1 at every set
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    #: The time of the last set.
    rotated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RemoteCredential(Base, PkMixin, TimestampMixin):
    """What the organisation holds to call remote agents over A2A (ADR 0062).

    No secret lives here: the refresh token (rotated at every refresh), the DCR client secret and a
    bearer token are sealed values of the secret store (`remote/<name>/…`, ADR 0058); this row keeps
    the reference, the public client registration and the state."""

    __tablename__ = "remote_credentials"
    __table_args__ = (UniqueConstraint("org_id", "name", name="uq_remote_credentials_org_name"),)

    org_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    #: `oauth2_authorization_code`, `bearer` or `none`.
    type: Mapped[str] = mapped_column(String(32))
    #: Public registration data: card URL, client id, token and authorisation endpoints, scopes,
    #: redirect URI. Never a secret.
    config: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    #: `store:remote/<name>/refresh_token` (OAuth) or `store:remote/<name>/token` (bearer); None before
    #: anyone authorised.
    secret_ref: Mapped[str | None] = mapped_column(String(240), nullable=True)
    authorised_as: Mapped[str | None] = mapped_column(String(320), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_refresh_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
