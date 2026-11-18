# SPDX-License-Identifier: Apache-2.0
"""Validation issues of an ontology package, located in their file (contract 03 §11.5)."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class Issue:
    """One error or warning, with a stable code (``ONT001``…) and its position when known."""

    code: str
    message: str
    file: str | None = None
    line: int | None = None
    column: int | None = None
    path: str | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "code": self.code,
            "message": self.message,
            "file": self.file,
            "line": self.line,
            "column": self.column,
            "path": self.path,
        }

    def format(self) -> str:
        where = self.file or ""
        if self.line is not None:
            where = f"{where}:{self.line}" + (f":{self.column}" if self.column is not None else "")
        if self.path:
            where = f"{where} ({self.path})" if where else self.path
        return f"[{self.code}] {self.message}" + (f" — {where}" if where else "")


@dataclass(slots=True)
class Validation:
    """The result of validating a package: every error found, not only the first one."""

    errors: list[Issue] = field(default_factory=list)
    warnings: list[Issue] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def to_dict(self) -> dict[str, object]:
        return {
            "valid": self.valid,
            "errors": [issue.to_dict() for issue in self.errors],
            "warnings": [issue.to_dict() for issue in self.warnings],
        }


class OntologyError(Exception):
    """Raised when a package cannot be compiled; carries the validation that refused it."""

    def __init__(self, validation: Validation) -> None:
        self.validation = validation
        first = validation.errors[0].format() if validation.errors else "invalid package"
        super().__init__(f"{len(validation.errors)} error(s); first: {first}")
