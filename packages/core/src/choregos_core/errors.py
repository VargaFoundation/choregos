"""Erreurs du cœur Choregos, localisées quand la source est connue."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True, frozen=True)
class Issue:
    """Une erreur ou un avertissement de validation, localisé si possible."""

    code: str
    message: str
    path: str | None = None
    line: int | None = None
    column: int | None = None

    def to_dict(self) -> dict[str, str | int | None]:
        return {
            "code": self.code,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "column": self.column,
        }

    def format(self) -> str:
        where = self.path or ""
        if self.line is not None:
            where = f"{where} (ligne {self.line}" + (f", colonne {self.column})" if self.column else ")")
        return f"[{self.code}] {self.message}" + (f" — {where}" if where.strip() else "")


class ChoregosError(Exception):
    """Base des erreurs Choregos."""


@dataclass
class ValidationError(ChoregosError):
    """Validation d'un document (workflow, politique, template) en échec."""

    issues: list[Issue] = field(default_factory=list)
    subject: str = "document"

    def __str__(self) -> str:
        head = f"{self.subject} invalide ({len(self.issues)} erreur(s)) :"
        return "\n".join([head, *[f"  - {i.format()}" for i in self.issues]])


class PolicyError(ChoregosError):
    """Violation de politique (budget, périmètre, approbation)."""


class ModelResolutionError(ChoregosError):
    """Un profil de modèle n'a pas pu être résolu."""


class GateError(ChoregosError):
    """Une gate inconnue ou mal paramétrée."""
