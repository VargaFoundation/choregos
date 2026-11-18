# SPDX-License-Identifier: Apache-2.0
"""A person's photo (S66-42, ADR 0162): uploaded by the person, re-encoded, served same-origin.

The console's Content-Security-Policy is `img-src 'self'`: a photo is never an address on another site, and
the API never fetches one (an identity provider's `picture` claim is not read; no outbound call goes to an
address someone else chose). What the person uploads is checked twice, by its first bytes and by decoding
it, then re-encoded as a square WebP of at most `STORED_SIDE` pixels: no metadata (a phone's GPS position)
and nothing of the original file survives, so a file that is also something else is not served back.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Final

from fastapi import status
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Membership, UserAvatar
from ..errors import ApiError, unprocessable
from ..rbac import Principal

#: The largest file accepted, before decoding.
MAX_BYTES: Final = 2 * 1024 * 1024
#: The bounds of each side of the uploaded image, in pixels: smaller is unreadable, larger is not a photo
#: of a person (and decoding it would cost the memory a decompression bomb aims at).
MIN_SIDE: Final = 32
MAX_SIDE: Final = 4096
#: The side of the stored square.
STORED_SIDE: Final = 512
STORED_TYPE: Final = "image/webp"
#: The formats accepted, by the signature of their first bytes.
ACCEPTED: Final = {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp"}


def sniffed_format(data: bytes) -> str | None:
    """The format the file's first bytes announce, among those accepted; `None` for any other."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "PNG"
    if data.startswith(b"\xff\xd8\xff"):
        return "JPEG"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "WEBP"
    return None


def too_large() -> ApiError:
    return ApiError(
        status.HTTP_413_CONTENT_TOO_LARGE,
        "Payload too large",
        f"a photo is at most {MAX_BYTES // (1024 * 1024)} MiB",
    )


def unsupported() -> ApiError:
    return ApiError(
        status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        "Unsupported media type",
        "a photo is a PNG, JPEG or WebP image",
    )


def reencode(data: bytes) -> bytes:
    """The uploaded image as the stored square WebP, or the refusal: 413 (size), 415 (type), 422 (a file
    that does not decode as the image it announces, or whose sides are out of bounds)."""
    if len(data) > MAX_BYTES:
        raise too_large()
    announced = sniffed_format(data)
    if announced is None:
        raise unsupported()
    try:
        with Image.open(io.BytesIO(data), formats=[announced]) as image:
            width, height = image.size
            if not (MIN_SIDE <= width <= MAX_SIDE and MIN_SIDE <= height <= MAX_SIDE):
                raise unprocessable(
                    f"a photo is {MIN_SIDE} to {MAX_SIDE} pixels a side; this one is {width}×{height}"
                )
            image.load()
            upright = ImageOps.exif_transpose(image)
            has_alpha = upright.mode in ("RGBA", "LA", "PA") or "transparency" in upright.info
            converted = upright.convert("RGBA" if has_alpha else "RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as broken:
        raise unprocessable(f"the file does not decode as a {announced} image") from broken
    side = min(converted.size)
    square = ImageOps.fit(converted, (side, side), method=Image.Resampling.LANCZOS)
    if side > STORED_SIDE:
        square = square.resize((STORED_SIDE, STORED_SIDE), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    square.save(out, format="WEBP", quality=85, method=4)
    return out.getvalue()


def avatar_path(user_id: str, digest: str) -> str:
    """The same-origin address the console writes in `<img src>`; the digest makes a new photo a new URL."""
    return f"/api/v1/users/{user_id}/avatar?v={digest[:12]}"


async def avatar_urls(session: AsyncSession, user_ids: list[str]) -> dict[str, str]:
    """The photo address of each person who has one, among `user_ids` (the session's RLS applies)."""
    if not user_ids:
        return {}
    rows = await session.execute(
        select(UserAvatar.user_id, UserAvatar.digest).where(UserAvatar.user_id.in_(user_ids))
    )
    return {str(user_id): avatar_path(str(user_id), str(digest)) for user_id, digest in rows.all()}


async def avatar_url(session: AsyncSession, user_id: str) -> str | None:
    return (await avatar_urls(session, [user_id])).get(user_id)


@dataclass(frozen=True)
class StoredAvatar:
    content: bytes
    content_type: str
    digest: str


async def store(session: AsyncSession, user_id: str, data: bytes) -> str:
    """Re-encodes and stores the person's photo, replacing theirs; returns its address."""
    content = reencode(data)
    digest = hashlib.sha256(content).hexdigest()
    row = await session.get(UserAvatar, user_id)
    if row is None:
        session.add(UserAvatar(user_id=user_id, content_type=STORED_TYPE, digest=digest, content=content))
    else:
        row.content_type, row.digest, row.content = STORED_TYPE, digest, content
    await session.flush()
    return avatar_path(user_id, digest)


async def remove(session: AsyncSession, user_id: str) -> bool:
    row = await session.get(UserAvatar, user_id)
    if row is None:
        return False
    await session.delete(row)
    return True


async def shares_an_organisation(session: AsyncSession, principal: Principal, user_id: str) -> bool:
    """Whether the person is a member (a role in it or on one of its projects) of an organisation the
    caller is a member of."""
    if principal.user_id == user_id:
        return True
    mine = select(Membership.org_id).where(Membership.user_id == principal.user_id)
    found = await session.execute(
        select(Membership.id).where(Membership.user_id == user_id, Membership.org_id.in_(mine)).limit(1)
    )
    return found.first() is not None


async def readable(session: AsyncSession, principal: Principal, user_id: str) -> StoredAvatar | None:
    """The person's photo, when the caller may see it: themself, or a member of an organisation they share.
    `None` otherwise, and when there is none: the route answers 404 either way."""
    if principal.kind == "outpost" or not await shares_an_organisation(session, principal, user_id):
        return None
    row = (
        await session.execute(
            select(UserAvatar.content, UserAvatar.content_type, UserAvatar.digest).where(
                UserAvatar.user_id == user_id
            )
        )
    ).first()
    if row is None:
        return None
    content, content_type, digest = row
    return StoredAvatar(content=bytes(content), content_type=str(content_type), digest=str(digest))
