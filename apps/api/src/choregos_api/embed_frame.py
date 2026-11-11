# SPDX-License-Identifier: Apache-2.0
"""The session of the embedded component in a cross-site frame (S63-12, ADR 0139 point 6).

The console's session cookie is `SameSite=Lax`: a browser does not send it to a page framed by another
site, and weakening it would change every console page. A third-party cookie of its own (`SameSite=None;
Partitioned`) was the other way; it is not used, because a cookie set by the sign-in popup (a first-party
page) is stored under the console's partition, not under the host's, and Safari refuses third-party cookies
whatever their attributes. What works with the existing auth is a **frame token**:

1. the person signs in in a popup of the console (first party, the ordinary session cookie);
2. the popup asks `POST /me/assistants/{org}/{slug}/embed-token` and hands the token to the frame that
   opened it (`postMessage`, same origin);
3. the frame sends it as `X-Choregos-Embed` on its requests.

The token is a signed value (purpose `embed-frame`), valid for fifteen minutes, bound to one person, one
browser session (ending that session ends the token), one organisation and one installation. It opens only
the few routes the component calls, the conversation must be the installation's, and the principal it makes
holds a role in that organisation only: a stolen token reads and writes nothing else, and cannot mint
another. A host application never sees it: it lives in the frame's memory.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .security import read_session, sign_session

PURPOSE = "embed-frame"
HEADER = "x-choregos-embed"
TTL_S = 900
_API = "/api/v1"
_SEGMENT = r"[A-Za-z0-9_.:-]+"


@dataclass(frozen=True, slots=True)
class FrameToken:
    user_id: str
    sid: str | None
    org: str
    slug: str
    installation_id: str
    issued_at: int


def mint(
    *, user_id: str, sid: str | None, org: str, slug: str, installation_id: str, settings: Settings
) -> str:
    claims: dict[str, Any] = {
        "sub": user_id,
        "org": org,
        "slug": slug,
        "inst": installation_id,
        "iat": int(time.time()),
        "exp": int(time.time()) + TTL_S,
    }
    if sid is not None:
        claims["sid"] = sid
    return sign_session(claims, settings, purpose=PURPOSE)


def read(token: str, settings: Settings) -> FrameToken | None:
    claims = read_session(token, settings, purpose=PURPOSE)
    if claims is None:
        return None
    fields = [claims.get(k) for k in ("sub", "org", "slug", "inst")]
    if not all(isinstance(f, str) and f for f in fields):
        return None
    sid = claims.get("sid")
    iat = claims.get("iat")
    return FrameToken(
        user_id=str(claims["sub"]),
        sid=sid if isinstance(sid, str) else None,
        org=str(claims["org"]),
        slug=str(claims["slug"]),
        installation_id=str(claims["inst"]),
        issued_at=int(iat) if isinstance(iat, int | float) else 0,
    )


def _relative(path: str) -> str:
    return path[len(_API) :] if path.startswith(_API + "/") else path


def route_of(method: str, path: str, token: FrameToken) -> tuple[bool, str | None]:
    """Is `method path` one the frame may call? And the conversation it names, which must be the
    installation's. Anything else (minting a token, deleting a conversation) is not."""
    relative = _relative(path).rstrip("/") or "/"
    own = f"/me/assistants/{re.escape(token.org)}/{re.escape(token.slug)}"
    if method == "GET" and relative == "/me":
        return True, None
    if method == "POST" and re.fullmatch(own + "/conversations", relative):
        return True, None
    if method == "GET" and re.fullmatch(own + f"/memory/{_SEGMENT}", relative):
        return True, None
    found = re.fullmatch(
        rf"/me/conversations/({_SEGMENT})(?:/(messages|cancel|actions|actions/{_SEGMENT}/(?:confirm|refuse)))?",
        relative,
    )
    if found is None:
        return False, None
    tail = found.group(2)
    if tail is None:
        return method == "GET", found.group(1)
    if tail == "actions":
        return method == "GET", found.group(1)
    return method == "POST", found.group(1)
