# SPDX-License-Identifier: Apache-2.0
"""Keyset pagination in SQL (ADR 0206): a list route reads one page from the database, never all of it.

The order is `(sort, *then, tiebreak)`: a timestamp, a name or a count, any further keys, then the primary
key so that rows sharing every value keep a total order. Each key may be a column, a column of a joined
table or any typed SQL expression (a `coalesce`, a `lower`): its value is selected with the page, so the
cursor never has to be read back off an entity. The cursor is the key of the last row returned, each value
tagged with its type and base64-encoded: opaque to the client, stable when rows are inserted ahead of it (an
offset is not), and read with an index instead of a scan. One more row than the page is fetched to know
whether another page follows, with no `COUNT(*)`.

`position_page` is the same contract over a list that is not a table: a snapshot a row already holds (the
skills, inputs or knowledge bases a run's `StageInput` names), cut by position.

A list that is not a SQL table (the project's configuration, the gateway, a catalogue merged from several
sources and cut by policy) is built whole, in the order it has always had, then filtered with `matches`
and cut with `position_page`: the cost is the build, the payload is one page.

`Page.slice` (`deps.py`) is what this replaces: it loaded up to a fixed cap in memory and cut it in Python,
so whatever lay beyond the cap was unreachable and every page paid for the whole read.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections import namedtuple
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ColumnElement, Select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute

from .errors import unprocessable
from .schemas import PageMeta

if TYPE_CHECKING:  # `deps` imports the services, which import this module
    from .deps import Page

#: A key of the order: a column, a column of a joined table, or a typed SQL expression.
Key = ColumnElement[Any] | QueryableAttribute[Any]
#: What a key's value may be.
KeyValue = datetime | int | str
#: Rows a `keep` filter looks at per round trip while it fills a page.
SCAN_BATCH = 200

_INVALID = "invalid cursor: pass back the meta.next_cursor of the previous page"
_TAGS: dict[str, type] = {"t": datetime, "i": int, "s": str}


def encode_cursor(*values: KeyValue) -> str:
    """Each value tagged with its type (`t` an instant, `i` a number, `s` a string), so that it comes back
    as the type it left as."""
    tagged: list[list[Any]] = []
    for value in values:
        if isinstance(value, datetime):
            tagged.append(["t", value.isoformat()])
        elif isinstance(value, int) and not isinstance(value, bool):
            tagged.append(["i", value])
        elif isinstance(value, str):
            tagged.append(["s", value])
        else:
            raise TypeError(f"a keyset cannot carry a {type(value).__name__}")
    return base64.urlsafe_b64encode(json.dumps(tagged).encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[KeyValue, ...]:
    """The key a cursor carries; 422 for anything this module did not write."""
    try:
        raw = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(raw, list) or not raw:
            raise ValueError("cursor shape")
        values: list[KeyValue] = []
        for item in raw:
            tag, value = item
            if tag == "t" and isinstance(value, str):
                values.append(datetime.fromisoformat(value))
            elif (tag == "i" and isinstance(value, int) and not isinstance(value, bool)) or (
                tag == "s" and isinstance(value, str)
            ):
                values.append(value)
            else:
                raise ValueError("cursor field")
        return tuple(values)
    except (binascii.Error, ValueError, TypeError, UnicodeDecodeError) as error:
        raise unprocessable(_INVALID) from error


def _expected(key: Key) -> type | None:
    """The type a key's cursor value must have, or `None` when the expression does not say."""
    try:
        python_type = key.type.python_type
    except NotImplementedError:
        return None
    return python_type if python_type in (datetime, int, str) else None


def _after(
    keys: Sequence[Key], values: Sequence[KeyValue], descending: Sequence[bool]
) -> ColumnElement[bool]:
    """The rows strictly after `values`: `k1 > v1 OR (k1 = v1 AND k2 > v2) OR …`, each in its direction."""
    clauses: list[ColumnElement[bool]] = []
    for index, key in enumerate(keys):
        beyond = key < values[index] if descending[index] else key > values[index]
        level = [keys[before] == values[before] for before in range(index)]
        clauses.append(and_(*level, beyond) if level else beyond)
    return or_(*clauses)


async def keyset_page(  # noqa: PLR0913 - each option is a route's real need (ADR 0206)
    session: AsyncSession,
    query: Select[*tuple[Any, ...]],
    *,
    sort: Key,
    tiebreak: Key,
    page: Page,
    then: Sequence[Key] = (),
    descending: bool = True,
    tiebreak_descending: bool | None = None,
    rows: bool = False,
    keep: Callable[[list[Any]], Awaitable[list[Any]]] | None = None,
) -> tuple[list[Any], PageMeta]:
    """One page of `query` (a `select`, filters already applied) and its meta.

    The order is `(sort, *then, tiebreak)`, descending unless `descending=False` (a name, a slug, or a log
    read oldest first); the tiebreak follows that direction unless `tiebreak_descending` says otherwise (a
    route whose order was `ts DESC, id ASC`).

    The rows come back as the `select` gives them: the entity for a one-entity `select`, else whole `Row`s
    (`rows=True`, or a `select` of several entities or columns) as named tuples of what the route
    selected, without the key columns this function adds to write the cursor.

    `keep` is for a rule SQL cannot express (a permission read from a JSON column): it receives a batch of
    rows in order and returns those that stay. The page is filled from successive batches of `SCAN_BATCH`
    rows after the cursor, so the cost is the rows scanned to find `limit + 1` that stay, never the table,
    and the cursor is the last row *returned*.
    """
    keys = [sort, *then, tiebreak]
    directions = [descending] * (len(keys) - 1) + [
        descending if tiebreak_descending is None else tiebreak_descending
    ]
    if page.cursor:
        values = decode_cursor(page.cursor)
        if len(values) != len(keys) or any(
            (kind := _expected(key)) is not None and not isinstance(value, kind)
            for key, value in zip(keys, values, strict=True)
        ):
            raise unprocessable(_INVALID)
        query = query.where(_after(keys, values, directions))
    # One per entity or expression the route selected (an entity is one element of a `Row`).
    width = len(query.column_descriptions)
    whole = rows or width > 1
    labelled = [key.label(f"_keyset_{index}") for index, key in enumerate(keys)]
    query = query.add_columns(*labelled).order_by(
        *[key.desc() if down else key.asc() for key, down in zip(keys, directions, strict=True)]
    )
    wanted = page.limit + 1
    result = await session.execute(query.limit(wanted if keep is None else SCAN_BATCH))
    shape = _shaper(list(result.keys())[:width], whole)
    if keep is None:
        fetched = list(result.all())
    else:
        fetched = []
        offset = 0
        batch = list(result.all())
        while True:
            kept = {id(item) for item in await keep([shape(row) for row in batch])}
            fetched += [row for row in batch if id(shape(row)) in kept]
            offset += SCAN_BATCH
            if len(fetched) >= wanted or len(batch) < SCAN_BATCH:
                break
            batch = list((await session.execute(query.limit(SCAN_BATCH).offset(offset))).all())
    has_more = len(fetched) > page.limit
    window = fetched[: page.limit]
    next_cursor = encode_cursor(*tuple(window[-1])[width:]) if has_more else None
    return [shape(row) for row in window], PageMeta(next_cursor=next_cursor, has_more=has_more)


def _shaper(names: list[str], whole: bool) -> Callable[[Any], Any]:
    """How a fetched row is handed back without the key columns: the entity of a one-entity `select`, or
    a named tuple of what the route selected (unpacked, indexed or read by name like a `Row`)."""
    if not whole:
        return lambda row: row[0]
    shaped = namedtuple("PageRow", names, rename=True)  # type: ignore[misc]
    width = len(names)
    cache: dict[int, Any] = {}

    def shape(row: Any) -> Any:
        # The same object for the same row, so that a `keep` filter is matched by identity.
        key = id(row)
        if key not in cache:
            cache[key] = shaped(*tuple(row)[:width])
        return cache[key]

    return shape


def position_page[T](items: Sequence[T], page: Page) -> tuple[list[T], PageMeta]:
    """One page of a list the caller already holds and that does not change while it is read.

    For a snapshot a row carries (the skills, inputs or knowledge bases a run's `StageInput` names, fixed
    when the run was prepared): the cursor is the position of the next item. The caller cuts BEFORE it
    reads what each item costs to read, so a page pays for its own items only.
    """
    start = 0
    if page.cursor:
        values = decode_cursor(page.cursor)
        position = values[0]
        if len(values) != 1 or not isinstance(position, int) or position < 0:
            raise unprocessable(_INVALID)
        start = position
    window = list(items[start : start + page.limit])
    has_more = start + page.limit < len(items)
    return window, PageMeta(
        next_cursor=encode_cursor(start + page.limit) if has_more else None, has_more=has_more
    )


def matches(q: str | None, *texts: str | None) -> bool:
    """The `q` filter of a list built in Python: no `q` keeps everything, else a row stays when any of its
    texts holds `q` as a substring, whatever the case (the same rule `ILIKE '%q%'` gives in SQL)."""
    needle = (q or "").strip().lower()
    return not needle or any(needle in text.lower() for text in texts if text)
