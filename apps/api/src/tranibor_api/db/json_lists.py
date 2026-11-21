# SPDX-License-Identifier: Apache-2.0
"""Filtering on a JSON column that holds a list of strings, in SQL, on PostgreSQL and SQLite alike."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import ColumnElement, String, cast


def has_tag(column: Any, tag: str) -> ColumnElement[bool]:
    """The list in `column` (a `Json` column of strings) carries `tag`, matched whole.

    A list holds only strings, so the JSON token `"tag"` appears in its text exactly when `tag` is one
    of its elements (a quote inside another string is escaped, and never reads as that token): the same
    plain `LIKE` serves `jsonb` and `json`, with `%` and `_` escaped.
    """
    return cast(column, String).contains(json.dumps(tag), autoescape=True)
