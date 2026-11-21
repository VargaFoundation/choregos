# SPDX-License-Identifier: Apache-2.0
"""Keyset pagination in SQL (ADR 0206): a list route reads one page from the database, never all of it.

The order is `(sort DESC, tiebreak DESC)` — a timestamp, then the primary key so that rows created in the
same instant keep a total order. The cursor is the `(sort, tiebreak)` pair of the last row returned,
base64-encoded: opaque to the client, stable when rows are inserted ahead of it (an offset is not), and
read with an index instead of a scan. One more row than the page is fetched to know whether another
page follows, with no `COUNT(*)`.

`Page.slice` (`deps.py`) is what this replaces: it loads up to a fixed cap in memory and cuts it in
Python, so whatever lies beyond the cap is unreachable and every page pays for the whole read.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import datetime
from typing import Any

from sqlalchemy import Select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from .deps import Page
from .errors import unprocessable
from .schemas import PageMeta


def encode_cursor(sort_value: datetime, tiebreak_value: str) -> str:
    raw = json.dumps([sort_value.isoformat(), tiebreak_value]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """The `(sort, tiebreak)` pair a cursor carries; 422 for anything this module did not write."""
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        sort_text, tiebreak = json.loads(raw)
        if not isinstance(sort_text, str) or not isinstance(tiebreak, str):
            raise ValueError("cursor fields")
        return datetime.fromisoformat(sort_text), tiebreak
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError) as error:
        raise unprocessable("invalid cursor: pass back the meta.next_cursor of the previous page") from error


async def keyset_page(
    session: AsyncSession,
    query: Select[Any],
    *,
    sort: InstrumentedAttribute[datetime],
    tiebreak: InstrumentedAttribute[str],
    page: Page,
) -> tuple[list[Any], PageMeta]:
    """One page of `query` (a `select` of ORM rows, filters already applied) and its meta."""
    if page.cursor:
        last_sort, last_tiebreak = decode_cursor(page.cursor)
        query = query.where(or_(sort < last_sort, and_(sort == last_sort, tiebreak < last_tiebreak)))
    query = query.order_by(sort.desc(), tiebreak.desc()).limit(page.limit + 1)
    rows = list((await session.execute(query)).scalars().all())
    has_more = len(rows) > page.limit
    window = rows[: page.limit]
    next_cursor = None
    if has_more:
        last = window[-1]
        next_cursor = encode_cursor(getattr(last, sort.key), getattr(last, tiebreak.key))
    return window, PageMeta(next_cursor=next_cursor, has_more=has_more)
