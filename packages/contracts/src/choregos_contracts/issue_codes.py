# SPDX-License-Identifier: Apache-2.0
"""Validation issue codes renamed to English (ADR 0046, S34-03).

A code is a key a client may match on, so a rename is a contract change. For one release the
platform emits the English code in `code` and the former French one in `deprecated_code`, and
`canonical_issue_code` accepts either spelling wherever a code is read.

S34-03 renamed `gate.sans_matiere` and `workflow.effet_implicite`; that window ended in S45-02.
`role.playbook_introuvable` is the last one (S45-02; remove in 0.22.0).
"""

from __future__ import annotations

#: Former code -> English code. The keys stay French on purpose: they are the aliases.
RENAMED_ISSUE_CODES: dict[str, str] = {
    "role.playbook_introuvable": "role.playbook_not_found",  # lang: fr-ok
}

#: English code -> former code, to emit both for one release.
_FORMER_CODES: dict[str, str] = {new: old for old, new in RENAMED_ISSUE_CODES.items()}


def canonical_issue_code(code: str) -> str:
    """The English code for `code`, whichever spelling it was given in."""
    return RENAMED_ISSUE_CODES.get(code, code)


def deprecated_issue_code(code: str) -> str | None:
    """The former spelling of a renamed code, or None when the code was never renamed."""
    return _FORMER_CODES.get(canonical_issue_code(code))


__all__ = ["RENAMED_ISSUE_CODES", "canonical_issue_code", "deprecated_issue_code"]
