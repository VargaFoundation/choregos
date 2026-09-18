"""Base SQLAlchemy : UUID v7, horodatage, JSON portable SQLite/PostgreSQL."""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import DateTime, MetaData, String, TypeDecorator, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def uuid7() -> str:
    """UUID v7 (RFC 9562) : trié par le temps, donc des index qui ne se fragmentent pas."""
    ms = int(time.time() * 1000)
    rand = os.urandom(10)
    value = (
        ms.to_bytes(6, "big")
        + bytes([(0x70 | (rand[0] & 0x0F)), rand[1]])
        + bytes([(0x80 | (rand[2] & 0x3F)), *rand[3:10]])
    )
    return str(uuid.UUID(bytes=value))


def utcnow() -> datetime:
    return datetime.now(UTC)


class Json(TypeDecorator[Any]):
    """`jsonb` sur PostgreSQL, `json` ailleurs."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Any) -> Any:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: Json,
        list[str]: Json,
        list[dict[str, Any]]: Json,
    }


class PkMixin:
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=uuid7)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), default=utcnow, onupdate=utcnow, nullable=False
    )
