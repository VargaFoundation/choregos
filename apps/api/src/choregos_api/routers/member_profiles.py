# SPDX-License-Identifier: Apache-2.0
"""A member's profile page and a person's photo (S66-42, ADR 0162).

- `GET /orgs/{org}/members/{user_id}/profile`: the page in one read, shaped by who reads it
  (`services/member_profiles.py` says who sees what).
- `PUT /me/avatar` (multipart `file`), `DELETE /me/avatar`: the caller's own photo, and nobody else's.
- `GET /users/{user_id}/avatar`: the photo, same-origin, for the person and the members of an organisation
  they share; 404 for anyone else.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from starlette.datastructures import UploadFile

from ..audit import record
from ..deps import Db, Me
from ..errors import not_found, unprocessable
from ..schemas import AvatarDto, MemberProfileDto
from ..services import avatars, member_profiles

router = APIRouter(tags=["admin"])
me_router = APIRouter(tags=["session"])

#: What a multipart envelope adds around the file, at most, before the size cap refuses it.
ENVELOPE = 16 * 1024


@router.get(
    "/orgs/{org}/members/{user_id}/profile",
    response_model=MemberProfileDto,
    operation_id="getMemberProfile",
)
async def get_member_profile(org: str, user_id: str, session: Db, principal: Me) -> MemberProfileDto:
    return await member_profiles.member_profile(session, principal, org, user_id)


@me_router.put("/me/avatar", response_model=AvatarDto, operation_id="putMyAvatar")
async def put_my_avatar(request: Request, session: Db, principal: Me) -> AvatarDto:
    """The caller's photo: decoded, bounded and re-encoded before it is stored (`services/avatars.py`)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > avatars.MAX_BYTES + ENVELOPE:
        raise avatars.too_large()
    async with request.form(max_files=1, max_fields=1) as form:
        upload = form.get("file")
        if not isinstance(upload, UploadFile):
            raise unprocessable("expected one image in the multipart field `file`")
        data = await upload.read(avatars.MAX_BYTES + 1)
    url = await avatars.store(session, principal.user_id, data)
    # A person's own photo is no organisation's: a platform event (`org_id=None`).
    await record(
        session, principal, "user.avatar.set", org_id=None, target_type="user", target_id=principal.user_id
    )
    return AvatarDto(avatar_url=url)


@me_router.delete("/me/avatar", status_code=status.HTTP_204_NO_CONTENT, operation_id="deleteMyAvatar")
async def delete_my_avatar(session: Db, principal: Me) -> Response:
    if await avatars.remove(session, principal.user_id):
        await record(
            session,
            principal,
            "user.avatar.remove",
            org_id=None,
            target_type="user",
            target_id=principal.user_id,
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@me_router.get(
    "/users/{user_id}/avatar",
    operation_id="getUserAvatar",
    response_class=Response,
    responses={200: {"content": {avatars.STORED_TYPE: {}}}},
)
async def get_user_avatar(user_id: str, request: Request, session: Db, principal: Me) -> Response:
    stored = await avatars.readable(session, principal, user_id)
    if stored is None:
        raise not_found("Avatar", user_id)
    etag = f'"{stored.digest}"'
    headers = {
        "ETag": etag,
        # Private: a photo is read by a signed-in person; the console's URL changes with the photo.
        "Cache-Control": "private, max-age=300",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "default-src 'none'; sandbox",
        "Content-Disposition": "inline",
    }
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    return Response(content=stored.content, media_type=stored.content_type, headers=headers)
