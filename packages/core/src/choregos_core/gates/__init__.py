"""Registre des gates : conditions déterministes portées par une transition.

Une gate est **le** mécanisme de garantie (docs/plan/01) : elle ne fait pas confiance
à l'agent, elle vérifie. Deux familles :

- **synchrones** : évaluables immédiatement à partir du `StageResult`, du diff et de la politique
  (`scope_respected`, `evidence_present`, `diff_size_max`, `no_secrets`, `coverage_delta_min`) ;
- **asynchrones** : elles attendent un événement externe (`ci_green`, `review_approved`,
  `scans_ok`, `provenance_signed`, `flag_present`, `external`).
"""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import StageResult

from ..errors import GateError


@dataclass(slots=True)
class GateContext:
    """Tout ce dont une gate synchrone a besoin pour trancher."""

    result: StageResult | None = None
    changed_files: list[str] = field(default_factory=list)
    additions: int = 0
    deletions: int = 0
    allowed_paths: list[str] = field(default_factory=list)
    secrets_found: list[str] = field(default_factory=list)
    ci_status: str | None = None
    review_state: str | None = None
    scans: dict[str, str] = field(default_factory=dict)
    signed: bool | None = None
    flags: list[str] = field(default_factory=list)
    required_flag: str | None = None
    external_results: dict[str, bool] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class GateOutcome:
    """Verdict d'une gate. `pending` = la gate attend un événement externe."""

    name: str
    passed: bool
    pending: bool = False
    detail: str = ""
    annotations: list[str] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        return not self.passed and not self.pending


GateFn = Callable[[GateContext, dict[str, Any]], GateOutcome]

_REGISTRY: dict[str, GateFn] = {}
_ASYNC_GATES: set[str] = set()


def gate(name: str, *, asynchronous: bool = False) -> Callable[[GateFn], GateFn]:
    def decorator(fn: GateFn) -> GateFn:
        _REGISTRY[name] = fn
        if asynchronous:
            _ASYNC_GATES.add(name)
        return fn

    return decorator


def known_gates() -> list[str]:
    return sorted(_REGISTRY)


def is_async_gate(name: str) -> bool:
    return name in _ASYNC_GATES


def evaluate(name: str, ctx: GateContext, params: dict[str, Any] | None = None) -> GateOutcome:
    """Évalue une gate du registre. Une gate inconnue est une erreur de configuration."""
    fn = _REGISTRY.get(name)
    if fn is None:
        raise GateError(f"gate inconnue : {name} (connues : {', '.join(known_gates())})")
    return fn(ctx, params or {})


def matches_any(path: str, patterns: list[str]) -> bool:
    """Un chemin est dans le périmètre si un motif glob le couvre (`src/**` couvre `src/a/b.py`)."""
    for pattern in patterns:
        if fnmatch.fnmatch(path, pattern):
            return True
        if pattern.endswith("/**") and (path == pattern[:-3] or path.startswith(pattern[:-2])):
            return True
        if pattern.endswith("**") and path.startswith(pattern[:-2]):
            return True
    return False


# ───────────────────────────── gates synchrones ─────────────────────────────


@gate("scope_respected")
def _scope_respected(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    allowed = params.get("paths") or ctx.allowed_paths
    if not allowed:
        return GateOutcome("scope_respected", True, detail="aucun périmètre déclaré")
    out = [p for p in ctx.changed_files if not matches_any(p, list(allowed))]
    return GateOutcome(
        "scope_respected",
        not out,
        detail="périmètre respecté" if not out else f"{len(out)} fichier(s) hors périmètre",
        annotations=out,
    )


@gate("evidence_present")
def _evidence_present(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if ctx.result is None:
        return GateOutcome("evidence_present", False, detail="aucun résultat d'étape")
    ev = ctx.result.evidence
    missing: list[str] = []
    if ev.tests_passed is None:
        missing.append("tests_passed")
    if ev.tests_run is None:
        missing.append("tests_run")
    if params.get("require_lint", False) and ev.lint is None:
        missing.append("lint")
    if params.get("require_typecheck", False) and ev.typecheck is None:
        missing.append("typecheck")
    if ev.tests_passed is False:
        return GateOutcome("evidence_present", False, detail="les tests échouent")
    return GateOutcome(
        "evidence_present",
        not missing,
        detail="preuves complètes" if not missing else f"preuves manquantes : {', '.join(missing)}",
    )


@gate("diff_size_max")
def _diff_size_max(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    max_files = int(params.get("files", params.get("n", 60)))
    max_lines = int(params.get("lines", 100_000))
    files = len(ctx.changed_files)
    lines = ctx.additions + ctx.deletions
    if files > max_files:
        return GateOutcome("diff_size_max", False, detail=f"{files} fichiers modifiés > {max_files}")
    if lines > max_lines:
        return GateOutcome("diff_size_max", False, detail=f"{lines} lignes modifiées > {max_lines}")
    return GateOutcome("diff_size_max", True, detail=f"{files} fichiers / {lines} lignes")


SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("clé AWS", r"AKIA[0-9A-Z]{16}"),
    ("clé privée", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("token GitHub", r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("clé Anthropic", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("clé OpenAI", r"sk-(?:proj-)?[A-Za-z0-9]{32,}"),
    ("token Slack", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("JWT", r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)


def scan_secrets(text: str) -> list[str]:
    """Détecteur de secrets volontairement simple : il complète gitleaks, il ne le remplace pas."""
    found: list[str] = []
    for label, pattern in SECRET_PATTERNS:
        if re.search(pattern, text):
            found.append(label)
    return found


@gate("no_secrets")
def _no_secrets(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    found = ctx.secrets_found
    return GateOutcome(
        "no_secrets",
        not found,
        detail="aucun secret détecté" if not found else f"secrets détectés : {', '.join(found)}",
        annotations=found,
    )


@gate("coverage_delta_min")
def _coverage_delta_min(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    threshold = float(params.get("x", params.get("min", 0.0)))
    if ctx.result is None or ctx.result.evidence.coverage_delta is None:
        return GateOutcome("coverage_delta_min", False, detail="delta de couverture inconnu")
    delta = ctx.result.evidence.coverage_delta
    return GateOutcome(
        "coverage_delta_min",
        delta >= threshold,
        detail=f"couverture {delta:+.2f} (seuil {threshold:+.2f})",
    )


# ───────────────────────────── gates asynchrones ─────────────────────────────


@gate("ci_green", asynchronous=True)
def _ci_green(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if ctx.ci_status is None:
        return GateOutcome("ci_green", False, pending=True, detail="CI en attente")
    ok = ctx.ci_status in {"success", "succeeded", "neutral"}
    return GateOutcome("ci_green", ok, detail=f"CI {ctx.ci_status}")


@gate("review_approved", asynchronous=True)
def _review_approved(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if ctx.review_state is None:
        return GateOutcome("review_approved", False, pending=True, detail="review en attente")
    ok = ctx.review_state == "approved"
    return GateOutcome("review_approved", ok, detail=f"review {ctx.review_state}")


@gate("scans_ok", asynchronous=True)
def _scans_ok(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    required = list(params.get("scanners", ["semgrep", "trivy", "gitleaks"]))
    if not ctx.scans:
        return GateOutcome("scans_ok", False, pending=True, detail="scans en attente")
    failed = [name for name in required if ctx.scans.get(name, "pending") not in {"ok", "passed", "skipped"}]
    pending = [name for name in required if ctx.scans.get(name) is None]
    if pending:
        return GateOutcome("scans_ok", False, pending=True, detail=f"scans en attente : {', '.join(pending)}")
    return GateOutcome(
        "scans_ok", not failed, detail="scans OK" if not failed else f"échec : {', '.join(failed)}"
    )


@gate("provenance_signed", asynchronous=True)
def _provenance_signed(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if ctx.signed is None:
        return GateOutcome("provenance_signed", False, pending=True, detail="signature en attente")
    return GateOutcome("provenance_signed", ctx.signed, detail="signé" if ctx.signed else "non signé")


@gate("flag_present", asynchronous=True)
def _flag_present(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Gate S9-06 : un ticket `risk: high` doit nommer un feature flag qui existe réellement."""
    name = params.get("name") or ctx.required_flag
    if not name:
        return GateOutcome("flag_present", False, detail="aucun feature flag nommé dans la spec")
    ok = name in ctx.flags
    return GateOutcome(
        "flag_present",
        ok,
        detail=f"flag `{name}` " + ("présent" if ok else "absent du code / du fournisseur de flags"),
    )


@gate("external", asynchronous=True)
def _external(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    url = str(params.get("url", ""))
    if url not in ctx.external_results:
        return GateOutcome("external", False, pending=True, detail=f"attente de {url}")
    ok = ctx.external_results[url]
    return GateOutcome("external", ok, detail=f"{url} → {'ok' if ok else 'ko'}")


__all__ = [
    "GateContext",
    "GateOutcome",
    "evaluate",
    "gate",
    "is_async_gate",
    "known_gates",
    "matches_any",
    "scan_secrets",
]
