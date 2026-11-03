# SPDX-License-Identifier: Apache-2.0
"""What the tracker knows about a ticket, as the work item carries it: `fields["tracker"]` (S51-04).

Who filed the ticket, who created it, its labels and the answers of its form (Jira's custom fields,
by their display name) reach the agents (`ticket.fields.tracker`) and the jobs
(`.choregos/fields.json`). One shape, whatever the tracker:

    {"kind": "jira", "key": "FON-270",
     "reporter": {"account_id": ..., "display_name": ..., "email": ...},
     "creator":  {"account_id": ..., "display_name": ..., "email": ...},
     "labels": [...],
     "answers": {"<display name>": "<text>", ...}}

Every value is third-party text: it is data an agent reads, never a template, a command or a path.
An email is the one the tracker returned, never a guess: Jira hides it unless the account allows it.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any

from choregos_core.domain import TRACKER_FIELD

from .adf import text_of_adf

#: The characters of one answer; a longer one is cut and ends with `…`.
MAX_ANSWER_CHARS = 2000
#: The characters of all the answers together; past it, the remaining answers are left out.
MAX_ANSWERS_CHARS = 20_000
#: At most this many answers.
MAX_ANSWERS = 100
#: The keys of an object that say what it is, in the order they are tried (an option, a user…).
_LABEL_KEYS = ("value", "name", "displayName", "title", "key")
_MAX_DEPTH = 8

Person = dict[str, str | None]


def person(raw: Any) -> Person:
    """A tracker account as `{account_id, display_name, email}`; each `None` when the tracker is silent."""
    found = raw if isinstance(raw, Mapping) else {}
    return {
        "account_id": _text_or_none(found.get("accountId")),
        "display_name": _text_or_none(found.get("displayName")),
        "email": _text_or_none(found.get("emailAddress")),
    }


def named_person(display_name: Any) -> Person:
    """A person the tracker names only by its display name (a plugin server's `author`)."""
    return {"account_id": None, "display_name": _text_or_none(display_name), "email": None}


def text_of(value: Any, depth: int = 0) -> str:
    """A field's value as text: an ADF document's text, an option's `value`, a user's name, a list
    joined with `, `; empty for nothing (and past a nesting no form produces)."""
    if value is None or depth > _MAX_DEPTH:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, str):
        # Jira's development panel answers `{}` when nothing is linked: nothing to say.
        return "" if value.strip() in {"{}", "[]"} else value.strip()
    if isinstance(value, list | tuple):
        return ", ".join(t for t in (text_of(v, depth + 1) for v in value) if t)
    if isinstance(value, Mapping):
        if value.get("type") == "doc":
            return text_of_adf(value)
        if isinstance(value.get("requestType"), Mapping):  # a service desk's request type
            return text_of(value["requestType"], depth + 1)
        for key in _LABEL_KEYS:
            label = text_of(value.get(key), depth + 1)
            if label:
                child = (
                    text_of(value.get("child"), depth + 1) if isinstance(value.get("child"), Mapping) else ""
                )
                return f"{label} / {child}" if child else label
        if not value:
            return ""
        try:
            return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        except (RecursionError, TypeError, ValueError):  # a nesting no form produces
            return ""
    return str(value).strip()


def capped(text: str, limit: int = MAX_ANSWER_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def answers(values: Iterable[tuple[str, Any]]) -> dict[str, str]:
    """`(display name, value)` pairs as answers: empty ones left out, each capped, the whole capped;
    two fields of the same name are told apart by a suffix."""
    out: dict[str, str] = {}
    total = 0
    for name, value in values:
        text = capped(text_of(value))
        label = " ".join(str(name).split())[:200]
        if not text or not label:
            continue
        if len(out) >= MAX_ANSWERS or total + len(text) > MAX_ANSWERS_CHARS:
            break
        unique, n = label, 2
        while unique in out:
            unique, n = f"{label} ({n})", n + 1
        out[unique] = text
        total += len(text)
    return out


def jira_answers(fields: Mapping[str, Any], names: Mapping[str, Any]) -> dict[str, str]:
    """Every non-empty `customfield_*` of a Jira issue, by its display name (`expand=names`), in the
    order of their names so the same issue always gives the same answers."""
    custom = [
        (str(names.get(field_id) or field_id), value)
        for field_id, value in fields.items()
        if field_id.startswith("customfield_")
    ]
    return answers(sorted(custom, key=lambda pair: pair[0].casefold()))


def tracker_block(
    *,
    kind: str,
    key: str,
    reporter: Person,
    creator: Person,
    labels: Iterable[Any],
    answers: Mapping[str, str],
) -> dict[str, Any]:
    """The `fields["tracker"]` of a work item; every key always present."""
    return {
        "kind": kind,
        "key": key,
        "reporter": dict(reporter),
        "creator": dict(creator),
        "labels": [str(label) for label in labels if label],
        "answers": dict(answers),
    }


def _text_or_none(value: Any) -> str | None:
    return (value.strip() or None) if isinstance(value, str) else None


__all__ = [
    "MAX_ANSWERS",
    "MAX_ANSWERS_CHARS",
    "MAX_ANSWER_CHARS",
    "TRACKER_FIELD",
    "answers",
    "capped",
    "jira_answers",
    "named_person",
    "person",
    "text_of",
    "tracker_block",
]
