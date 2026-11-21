# SPDX-License-Identifier: Apache-2.0
"""One page of a memory's review queue (ADR 0206): the proposals waiting for a decision, oldest first.

A backend pages its own queue: the lexical memory by keyset in SQL, Ecphoria by the cursor of its
`GET /api/v1/pending`. The cursor is the backend's, opaque to the API that passes it on; a cursor the
backend did not write raises `InvalidCursorError`, which the API answers with a 422.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass
from datetime import datetime

from tranibor_core.domain import Memory

#: The page size a whole-queue read asks for (the API's own ceiling).
WHOLE_QUEUE_PAGE = 200


class InvalidCursorError(ValueError):
    """A cursor this backend did not write."""


@dataclass(frozen=True, slots=True)
class PendingPage:
    items: list[Memory]
    next_cursor: str | None


def encode_position(created_at: datetime, memory_id: str) -> str:
    raw = json.dumps([created_at.isoformat(), memory_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_position(cursor: str) -> tuple[datetime, str]:
    try:
        when, memory_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(when, str) or not isinstance(memory_id, str):
            raise ValueError("cursor fields")
        return datetime.fromisoformat(when), memory_id
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError) as error:
        raise InvalidCursorError(cursor) from error
