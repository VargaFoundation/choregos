# SPDX-License-Identifier: Apache-2.0
"""Errors of the Choregos core, located when the source is known."""

from __future__ import annotations

from dataclasses import dataclass, field

from choregos_contracts.issue_codes import deprecated_issue_code


@dataclass(slots=True, frozen=True)
class Issue:
    """A validation error or warning, located when possible."""

    code: str
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        issue: dict[str, str | int | None] = {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
        }
        # A renamed code also carries its former spelling, for one release (ADR 0046).
        if (former := deprecated_issue_code(self.code)) is not None:
            issue["deprecated_code"] = former
        return issue

    def format(self) -> str:
        where = self.path or ""
        if self.line is not None:
            where = f"{where} (line {self.line}" + (f", column {self.column})" if self.column else ")")
        return f"[{self.code}] {self.message}" + (f" — {where}" if where.strip() else "")


class ChoregosError(Exception):
    """Base of the Choregos errors."""


@dataclass
class ValidationError(ChoregosError):
    """Validation of a document (workflow, policy, template) failed."""

    issues: list[Issue] = field(default_factory=list)
    subject: str = "document"

    def __str__(self) -> str:
        head = f"invalid {self.subject} ({len(self.issues)} error(s)):"
        return "\n".join([head, *[f"  - {i.format()}" for i in self.issues]])


class PolicyError(ChoregosError):
    """Policy violation (budget, perimeter, approval)."""


class ModelResolutionError(ChoregosError):
    """A model profile could not be resolved."""


class GateError(ChoregosError):
    """An unknown gate, or one with wrong parameters."""
