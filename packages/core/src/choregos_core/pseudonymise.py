# SPDX-License-Identifier: Apache-2.0
"""Pseudonymisation of a conversation before it becomes a candidate eval case (ADR 0140).

A thumb down proposes the conversation to the installation's owners as a candidate case. Before it is
stored, what identifies a person is replaced by a stable placeholder: the same value is the same
placeholder everywhere in one conversation (so the scenario still makes sense), and no placeholder
depends on the value. What is replaced:

- email addresses and user principal names (`<email_1>`),
- the names the caller gives (the person's own, the people the conversation is about: `<person_1>`),
- IBANs (`<iban_1>`) and long digit runs such as phone or account numbers (`<number_1>`).

It is a safety net, not an anonymisation guarantee: free text may still identify someone. That is why a
candidate is read by an owner and never enters a suite on its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,3})?\b")
#: Seven digits or more, possibly spaced or dotted: a phone, an account or a social security number.
LONG_NUMBER = re.compile(r"(?<![\w.])\+?\d(?:[ .-]?\d){6,}(?![\w])")


class Pseudonymiser:
    """Replaces identifying values with placeholders, numbered in order of first appearance."""

    def __init__(self, names: Iterable[str] = ()) -> None:
        # Longest first, so that "Ada Lovelace" is replaced before "Ada".
        self._names = sorted({n.strip() for n in names if len(n.strip()) >= 2}, key=len, reverse=True)
        self._seen: dict[tuple[str, str], str] = {}

    def _placeholder(self, kind: str, value: str) -> str:
        key = (kind, value.casefold())
        if key not in self._seen:
            count = sum(1 for k, _ in self._seen if k == kind) + 1
            self._seen[key] = f"<{kind}_{count}>"
        return self._seen[key]

    def text(self, text: str) -> str:
        """`text` with every identifying value replaced."""
        text = EMAIL.sub(lambda m: self._placeholder("email", m.group(0)), text)
        for name in self._names:
            pattern = re.compile(rf"(?<!\w){re.escape(name)}(?!\w)", re.IGNORECASE)
            text = pattern.sub(lambda m: self._placeholder("person", m.group(0)), text)
        text = IBAN.sub(lambda m: self._placeholder("iban", m.group(0)), text)
        return LONG_NUMBER.sub(lambda m: self._placeholder("number", m.group(0)), text)

    def value(self, value: object) -> object:
        """`value` (JSON-like) with every string inside it pseudonymised."""
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {key: self.value(item) for key, item in value.items()}
        return value
