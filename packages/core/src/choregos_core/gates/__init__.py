# SPDX-License-Identifier: Apache-2.0
"""Registre des gates : conditions déterministes portées par une transition.

Une gate est **le** mécanisme de garantie (docs/plan/01) : elle ne fait pas confiance
à l'agent, elle vérifie. Deux familles :

- **synchrones** : évaluables immédiatement à partir du `StageResult`, du diff et de la politique
  (`scope_respected`, `evidence_present`, `diff_size_max`, `no_secrets`, `coverage_delta_min`,
  `adr_number_free`…) ;
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
    # Les sorties que la transition déclare (`outputs:`), lues par `outputs_present`.
    expected_outputs: list[str] = field(default_factory=list)
    #: Les outils du catalogue que ce run a APPELÉS (noms du catalogue), lus au registre
    #: de coûts — jamais dans ce que l'agent raconte. `tool_called` s'en sert.
    tool_calls: list[str] = field(default_factory=list)
    #: Le diff a-t-il PU être obtenu ? Faux quand le connecteur SCM ne sait pas comparer
    #: (fake, panne, dépôt injoignable). Une garantie qui repose sur le diff ne doit alors
    #: pas se prononcer : sans diff, `scope_respected` déclarerait le périmètre respecté et
    #: `no_secrets` l'absence de secrets, faute d'avoir regardé quoi que ce soit.
    diff_available: bool = True
    #: L'état de l'action gouvernée que la transition a proposée (S20-05) — lu en base, jamais
    #: déduit : `action_succeeded` ne passe que sur `succeeded`.
    action_status: str | None = None
    #: Le texte que la branche AJOUTE, fichier par fichier : les lignes `+` du diff cumulé (un
    #: fichier neuf l'est tout entier). `markdown_sections` lit ce qui est écrit, pas ce que
    #: l'agent raconte en avoir écrit.
    added_text: dict[str, str] = field(default_factory=dict)
    #: Les chemins présents sur la branche PAR DÉFAUT, sous le dossier que lit la garantie
    #: (`adr_number_free`) ; `None` quand le connecteur SCM n'a pas pu les lister.
    default_branch_files: list[str] | None = None


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
_NEEDS: dict[str, frozenset[str]] = {}


def gate(name: str, *, asynchronous: bool = False, needs: tuple[str, ...] = ()) -> Callable[[GateFn], GateFn]:
    """`needs` : les capacités de connecteur que la garantie lit (ADR 0034) — un diff vient du
    `scm`, un statut de pipeline de la `ci`. Un workflow qui la pose les exige de son projet."""

    def decorator(fn: GateFn) -> GateFn:
        _REGISTRY[name] = fn
        if asynchronous:
            _ASYNC_GATES.add(name)
        _NEEDS[name] = frozenset(needs)
        return fn

    return decorator


def gate_needs(name: str) -> frozenset[str]:
    """Les capacités qu'une garantie lit ; une garantie inconnue n'en exige aucune."""
    return _NEEDS.get(name, frozenset())


def known_gates() -> list[str]:
    return sorted(_REGISTRY)


def is_async_gate(name: str) -> bool:
    return name in _ASYNC_GATES


def evaluate(name: str, ctx: GateContext, params: dict[str, Any] | None = None) -> GateOutcome:
    """Évalue une gate du registre. Une gate inconnue est une erreur de configuration."""
    fn = _REGISTRY.get(name)
    if fn is None:
        raise GateError(f"unknown gate: {name} (known: {', '.join(known_gates())})")
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


def _sans_diff(name: str) -> GateOutcome:
    return GateOutcome(
        name,
        False,
        detail="diff unavailable: this guarantee could not be evaluated (SCM connector)",
    )


@gate("scope_respected", needs=("scm",))
def _scope_respected(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if not ctx.diff_available:
        return _sans_diff("scope_respected")
    allowed = params.get("paths") or ctx.allowed_paths
    if not allowed:
        return GateOutcome("scope_respected", True, detail="no allowed paths declared")
    out = [p for p in ctx.changed_files if not matches_any(p, list(allowed))]
    return GateOutcome(
        "scope_respected",
        not out,
        detail="within the allowed paths" if not out else f"{len(out)} file(s) outside the allowed paths",
        annotations=out,
    )


@gate("evidence_present")
def _evidence_present(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if ctx.result is None:
        return GateOutcome("evidence_present", False, detail="no stage result")
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
        return GateOutcome("evidence_present", False, detail="tests are failing")
    return GateOutcome(
        "evidence_present",
        not missing,
        detail="evidence complete" if not missing else f"missing evidence: {', '.join(missing)}",
    )


@gate("outputs_in")
def _outputs_in(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Les sorties nommées prennent une valeur permise : `{values: {verdict: [approve]}}`.

    Le moteur ne lit pas le verdict d'une revue : une revue qui demandait des changements passait
    `outputs_present` (la sortie existe) et le ticket avançait. Ici, la valeur compte.
    """
    permises: dict[str, list[Any]] = dict(params.get("values") or {})
    if not permises:
        return GateOutcome(
            "outputs_in", False, detail="no value required: `values:` missing on the guarantee"
        )
    if ctx.result is None:
        return GateOutcome("outputs_in", False, detail="no stage result")
    produites = ctx.result.outputs.model_dump(exclude_none=True)
    ecarts = [
        f"{cle} = {produites.get(cle)!r} (allowed: {', '.join(str(v) for v in valeurs)})"
        for cle, valeurs in permises.items()
        if str(produites.get(cle)) not in {str(v) for v in valeurs}
    ]
    return GateOutcome(
        "outputs_in",
        not ecarts,
        detail="outputs within the allowed values" if not ecarts else "; ".join(ecarts),
        annotations=ecarts,
    )


def _titres(texte: str) -> set[str]:
    """Les titres Markdown d'un texte, sans dièses ni casse : `## Decision Outcome` → `decision outcome`."""
    return {
        ligne.lstrip("#").strip().lower()
        for ligne in texte.splitlines()
        if ligne.startswith("#") and ligne.lstrip("#").startswith(" ")
    }


@gate("markdown_sections", needs=("scm",))
def _markdown_sections(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Les documents que la branche ajoute portent leurs sections : `{paths, sections, patterns,
    min_files}`. Un ADR au format MADR 4 doit dire son contexte, ses options et sa décision — et le
    dire DANS le fichier, lu dans le diff, pas dans le résumé de l'agent."""
    if not ctx.diff_available:
        return _sans_diff("markdown_sections")
    chemins = [str(c) for c in params.get("paths") or []]
    if not chemins:
        return GateOutcome("markdown_sections", False, detail="no path: `paths:` missing on the guarantee")
    fichiers = {chemin: texte for chemin, texte in ctx.added_text.items() if matches_any(chemin, chemins)}
    minimum = int(params.get("min_files", 1))
    if len(fichiers) < minimum:
        return GateOutcome(
            "markdown_sections",
            False,
            detail=f"{len(fichiers)} file(s) matching {', '.join(chemins)} in the change, {minimum} expected",
        )
    manques = []
    for chemin, texte in sorted(fichiers.items()):
        titres = _titres(texte)
        manques += [
            f"{chemin}: section “{s}” missing"
            for s in params.get("sections") or []
            if str(s).lower() not in titres
        ]
        manques += [
            f"{chemin}: {m!r} not found" for m in params.get("patterns") or [] if not re.search(str(m), texte)
        ]
    return GateOutcome(
        "markdown_sections",
        not manques,
        detail=f"{len(fichiers)} document(s) with their sections" if not manques else "; ".join(manques),
        annotations=manques,
    )


#: Le motif par défaut d'un ADR numéroté : `docs/adr/0042-titre.md`.
MOTIF_ADR = "docs/adr/[0-9][0-9][0-9][0-9]-*.md"


def _numero(chemin: str) -> int | None:
    """Le numéro en tête du nom de fichier : `docs/adr/0042-rls.md` → 42."""
    trouve = re.match(r"(\d+)-", chemin.rsplit("/", 1)[-1])
    return int(trouve.group(1)) if trouve else None


@gate("adr_number_free", needs=("scm",))
def _adr_number_free(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Le numéro de l'ADR que la branche ajoute n'est pas déjà pris sur la branche par défaut (#287).

    L'architecte numérote « le plus grand existant plus un », lu au moment où il écrit : deux études
    qui tournent ensemble prennent le même numéro, et la seconde fusionnerait un doublon. Rien ne
    réserve un numéro ; cette garantie, posée à la fusion, refuse le doublon au dernier moment utile.
    Un chemin déjà présent tel quel sur la branche par défaut n'est pas un ajout : c'est le même
    fichier (ou un conflit d'ajout que la forge refuse d'elle-même).
    """
    if not ctx.diff_available:
        return _sans_diff("adr_number_free")
    motif = str(params.get("pattern") or MOTIF_ADR)
    if ctx.default_branch_files is None:
        if not any(fnmatch.fnmatch(p, motif) for p in ctx.changed_files):
            return GateOutcome("adr_number_free", True, detail="no numbered record added by the change")
        return GateOutcome(
            "adr_number_free",
            False,
            detail="default branch unavailable: the numbers already taken could not be listed "
            "(SCM connector)",
        )
    existants = sorted(p for p in ctx.default_branch_files if fnmatch.fnmatch(p, motif))
    ajoutes = [p for p in ctx.changed_files if fnmatch.fnmatch(p, motif) and p not in existants]
    if not ajoutes:
        return GateOutcome("adr_number_free", True, detail="no numbered record added by the change")
    pris: dict[int, list[str]] = {}
    for chemin in existants:
        numero = _numero(chemin)
        if numero is not None:
            pris.setdefault(numero, []).append(chemin)
    doublons = [
        (chemin, numero, pris[numero])
        for chemin in sorted(ajoutes)
        if (numero := _numero(chemin)) is not None and numero in pris
    ]
    if not doublons:
        return GateOutcome(
            "adr_number_free",
            True,
            detail=f"number free on the default branch: {', '.join(sorted(ajoutes))}",
        )
    libre = max(pris) + 1
    return GateOutcome(
        "adr_number_free",
        False,
        detail="; ".join(
            f"{chemin}: number {numero:04d} is already taken on the default branch by {', '.join(autres)}"
            for chemin, numero, autres in doublons
        )
        + f" — renumber it (next free number: {libre:04d})",
        annotations=[chemin for chemin, _, _ in doublons],
    )


@gate("outputs_present")
def _outputs_present(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Les sorties déclarées de l'étape existent et ne sont pas vides.

    La garantie générique du moteur : `evidence_present` parle de tests et de couverture,
    donc de logiciel. Une étape qui produit une liste de candidats, un courrier ou un
    dossier instruit n'a pas de tests — elle a des SORTIES, que la transition nomme
    (`outputs:`) et que celle-ci vérifie. Sans elle, un workflow hors logiciel n'aurait
    aucune garantie mécanique, et il faudrait croire l'agent sur parole.
    """
    required = [str(k) for k in (params.get("keys") or ctx.expected_outputs)]
    if not required:
        # Symétrie avec `_sans_diff` : une garantie DEMANDÉE qui n'a rien à regarder ne
        # passe pas, elle refuse. Livrée le 2026-09-23, celle-ci passait — le même défaut
        # que celui qu'on venait de corriger sur les garanties de diff, à un jour près.
        # `allow_empty: true` reste possible pour une transition dont les sorties sont
        # facultatives, mais il faut alors l'écrire.
        if params.get("allow_empty"):
            return GateOutcome("outputs_present", True, detail="no output declared (tolerated)")
        return GateOutcome(
            "outputs_present",
            False,
            detail="no output declared: the transition asks for this guarantee without saying "
            "what to produce (`outputs:`), or `allow_empty: true` to accept it",
        )
    if ctx.result is None:
        return GateOutcome("outputs_present", False, detail="no stage result")
    # `StageOutputs` tolère les champs supplémentaires (rôles custom) : c'est ce qui permet
    # à un métier de nommer ses propres sorties sans toucher au contrat.
    produced = ctx.result.outputs.model_dump(exclude_none=True)
    missing = [key for key in required if not produced.get(key)]
    return GateOutcome(
        "outputs_present",
        not missing,
        detail="outputs present" if not missing else f"missing outputs: {', '.join(missing)}",
        annotations=missing,
    )


#: Les états d'une action qui ne bougeront plus.
ACTION_REGLEE = frozenset({"succeeded", "failed", "rejected"})


@gate("action_succeeded", asynchronous=True)
def _action_succeeded(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """L'action gouvernée de la transition a RÉUSSI : chacun de ses effets fait, confirmé (ADR 0035).

    Rejetée, échouée — et alors compensée —, elle bloque ; proposée, en attente d'une décision ou
    en cours, elle attend. Une transition sans action n'a rien à juger : le validateur le refuse.
    """
    statut = ctx.action_status
    if statut is None or statut not in ACTION_REGLEE:
        return GateOutcome("action_succeeded", False, pending=True, detail=f"action {statut or 'to propose'}")
    if statut == "succeeded":
        return GateOutcome("action_succeeded", True, detail="action succeeded")
    return GateOutcome("action_succeeded", False, detail=f"action {statut}")


@gate("tool_called")
def _tool_called(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Les outils que la transition exige ont été appelés — au registre, pas dans le récit.

    Sur le banc du 2026-09-24 le playbook de sourcing disait « vérifie le lieu avec
    `verifier_adresse` avant toute chose », l'agent consignait `lieu_verifie: true`, et le
    registre n'avait aucun appel : dix listages du catalogue, zéro appel. Un outil annoncé
    dans un prompt n'est pas un outil employé. Cette garantie lit les appels que la
    plateforme a elle-même comptés (ADR 0014) : on ne croit pas l'agent, on le mesure.
    """
    exiges = [str(t) for t in (params.get("tools") or [])]
    if not exiges:
        return GateOutcome(
            "tool_called",
            False,
            detail="no tool required: the guarantee needs `tools: [...]` to have something to check",
        )
    appeles = set(ctx.tool_calls)
    manquants = [t for t in exiges if t not in appeles]
    return GateOutcome(
        "tool_called",
        not manquants,
        detail=(
            f"tools called: {', '.join(exiges)}"
            if not manquants
            else f"tools required but never called (per the ledger): {', '.join(manquants)}"
        ),
        annotations=manquants,
    )


@gate("evidence_facts")
def _evidence_facts(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Les preuves que le métier a nommées sont là, et comparables si on le demande.

    `evidence_present` exige des tests : c'est la preuve du logiciel. `outputs_present`
    vérifie qu'une étape a produit ses sorties, mais une sortie est ce que l'agent
    RACONTE. Entre les deux manquait ce qu'un dossier instruit peut offrir de mesurable :
    des faits nommés — `profils_retenus: 3`, `piece_identite: true`, `delai_jours: 12` —
    consignés dans `evidence.facts` et vérifiés ici.

        gates:
          - name: evidence_facts
            params: { keys: [profils_retenus], min: { profils_retenus: 1 }, must_be_true: [besoin_complet] }

    `min` refuse un zéro poli : un agent qui ne trouve personne doit le dire en échouant
    son étape, pas en rendant une preuve vide qui passe.
    """
    required = [str(k) for k in (params.get("keys") or [])]
    if not required:
        if params.get("allow_empty"):
            return GateOutcome("evidence_facts", True, detail="no fact required (tolerated)")
        return GateOutcome(
            "evidence_facts", False, detail="no fact required: `keys:` missing on the guarantee"
        )
    if ctx.result is None:
        return GateOutcome("evidence_facts", False, detail="no stage result")
    facts = ctx.result.evidence.facts or {}
    manquants = [key for key in required if key not in facts]
    if manquants:
        return GateOutcome(
            "evidence_facts",
            False,
            detail=f"missing facts: {', '.join(manquants)}",
            annotations=manquants,
        )
    # Un booléen est un entier en Python : `piece_identite: false` passerait un `min: 0`
    # sans qu'on l'ait voulu. On ne compare donc que ce qui est un nombre pour de bon.
    insuffisants = [
        f"{key} = {facts[key]} < {seuil}"
        for key, seuil in (params.get("min") or {}).items()
        if key in facts
        and isinstance(facts[key], int | float)
        and not isinstance(facts[key], bool)
        and float(facts[key]) < float(seuil)
    ]
    # `must_be_true` et pas `true` : en YAML, une clé `true:` est lue comme le BOOLÉEN
    # `True`, et le workflow est refusé avec un message sur les types. Un nom de
    # paramètre ne doit pas être un mot réservé du format qui le porte.
    faux = [key for key in (params.get("must_be_true") or []) if facts.get(key) is not True]
    if insuffisants or faux:
        detail = "; ".join(
            filter(None, [", ".join(insuffisants), ("false: " + ", ".join(faux)) if faux else ""])
        )
        return GateOutcome("evidence_facts", False, detail=detail, annotations=insuffisants + faux)
    return GateOutcome("evidence_facts", True, detail=f"facts checked: {', '.join(sorted(required))}")


@gate("diff_size_max", needs=("scm",))
def _diff_size_max(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if not ctx.diff_available:
        return _sans_diff("diff_size_max")
    max_files = int(params.get("files", params.get("n", 60)))
    max_lines = int(params.get("lines", 100_000))
    files = len(ctx.changed_files)
    lines = ctx.additions + ctx.deletions
    if files > max_files:
        return GateOutcome("diff_size_max", False, detail=f"{files} files changed > {max_files}")
    if lines > max_lines:
        return GateOutcome("diff_size_max", False, detail=f"{lines} lines changed > {max_lines}")
    return GateOutcome("diff_size_max", True, detail=f"{files} files / {lines} lines")


SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("AWS key", r"AKIA[0-9A-Z]{16}"),
    ("private key", r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    ("GitHub token", r"gh[pousr]_[A-Za-z0-9]{20,}"),
    ("Anthropic key", r"sk-ant-[A-Za-z0-9_-]{20,}"),
    ("OpenAI key", r"sk-(?:proj-)?[A-Za-z0-9]{32,}"),
    ("Slack token", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("JWT", r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)


def scan_secrets(text: str) -> list[str]:
    """Détecteur de secrets volontairement simple : il complète gitleaks, il ne le remplace pas."""
    found: list[str] = []
    for label, pattern in SECRET_PATTERNS:
        if re.search(pattern, text):
            found.append(label)
    return found


@gate("no_secrets", needs=("scm",))
def _no_secrets(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if not ctx.diff_available:
        return _sans_diff("no_secrets")
    found = ctx.secrets_found
    return GateOutcome(
        "no_secrets",
        not found,
        detail="no secret detected" if not found else f"secrets detected: {', '.join(found)}",
        annotations=found,
    )


# La couverture que le runner MESURE dans le dépôt cloné : un dépôt, pas une CI (S21-23).
@gate("coverage_delta_min", needs=("scm",))
def _coverage_delta_min(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    threshold = float(params.get("x", params.get("min", 0.0)))
    if ctx.result is None or ctx.result.evidence.coverage_delta is None:
        return GateOutcome("coverage_delta_min", False, detail="coverage delta unknown")
    delta = ctx.result.evidence.coverage_delta
    return GateOutcome(
        "coverage_delta_min",
        delta >= threshold,
        detail=f"coverage {delta:+.2f} (threshold {threshold:+.2f})",
    )


# ───────────────────────────── gates asynchrones ─────────────────────────────


# `ci_green` et `scans_ok` lisent les checks de la PR À TRAVERS LE DÉPÔT (`scm.get_pr`, dans
# `activities/gates.py`) — GitHub Actions comme Tekton. Ils déclaraient un `ci` que rien ne lisait :
# un projet sur GitHub Actions se voyait réclamer un Tekton qu'il n'a pas (S21-23).
@gate("ci_green", asynchronous=True, needs=("scm",))
def _ci_green(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    # `pending` est ce que rend `checks_conclusion` tant qu'un check tourne : le prendre pour un
    # échec escaladait un ticket dont la CI, verte 13 s plus tard, n'avait pas fini (#5 du
    # locataire dev, 09/10 — S22-14).
    if ctx.ci_status in (None, "pending"):
        return GateOutcome("ci_green", False, pending=True, detail="CI pending")
    ok = ctx.ci_status in {"success", "succeeded", "neutral"}
    return GateOutcome("ci_green", ok, detail=f"CI {ctx.ci_status}")


@gate("review_approved", asynchronous=True, needs=("scm",))
def _review_approved(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if ctx.review_state is None:
        return GateOutcome("review_approved", False, pending=True, detail="review pending")
    ok = ctx.review_state == "approved"
    return GateOutcome("review_approved", ok, detail=f"review {ctx.review_state}")


@gate("scans_ok", asynchronous=True, needs=("scm",))
def _scans_ok(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    required = list(params.get("scanners") or sorted(ctx.scans) or ["semgrep", "trivy", "gitleaks"])
    if not ctx.scans:
        if ctx.ci_status in {"success", "failure", "neutral"}:
            return GateOutcome(
                "scans_ok",
                False,
                detail="no security scan declared on the PR: add semgrep/trivy/gitleaks "
                "to the CI, or remove the `scans_ok` gate from the workflow",
            )
        return GateOutcome("scans_ok", False, pending=True, detail="scans pending")
    failed = [name for name in required if ctx.scans.get(name, "pending") not in {"ok", "passed", "skipped"}]
    pending = [name for name in required if ctx.scans.get(name) is None]
    if pending:
        return GateOutcome("scans_ok", False, pending=True, detail=f"scans pending: {', '.join(pending)}")
    return GateOutcome(
        "scans_ok", not failed, detail="scans OK" if not failed else f"failed: {', '.join(failed)}"
    )


# La provenance se lit sur la PR, comme les scans : un check dont le nom contient `provenance`
# (`attest-build-provenance`, `slsa-provenance`…). Rien ne renseignait `signed` : la garantie
# attendait pour toujours, et un ticket `advanced` ne fusionnait jamais (#283).
@gate("provenance_signed", asynchronous=True, needs=("scm",))
def _provenance_signed(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    if ctx.signed is None:
        if ctx.ci_status in {"success", "failure", "neutral", "succeeded"}:
            return GateOutcome(
                "provenance_signed",
                False,
                detail="no provenance check on the pull request: add one to the CI (a check named "
                "`provenance`), or remove the `provenance_signed` guarantee from the workflow",
            )
        return GateOutcome("provenance_signed", False, pending=True, detail="provenance pending")
    return GateOutcome("provenance_signed", ctx.signed, detail="signed" if ctx.signed else "not signed")


@gate("flag_present", asynchronous=True)
def _flag_present(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    """Gate S9-06 : un ticket `risk: high` doit nommer un feature flag qui existe réellement."""
    name = params.get("name") or ctx.required_flag
    if not name:
        return GateOutcome("flag_present", False, detail="no feature flag named in the spec")
    ok = name in ctx.flags
    return GateOutcome(
        "flag_present",
        ok,
        detail=f"flag `{name}` " + ("present" if ok else "missing from the code / the flag provider"),
    )


@gate("external", asynchronous=True)
def _external(ctx: GateContext, params: dict[str, Any]) -> GateOutcome:
    url = str(params.get("url", ""))
    if url not in ctx.external_results:
        return GateOutcome("external", False, pending=True, detail=f"waiting for {url}")
    ok = ctx.external_results[url]
    return GateOutcome("external", ok, detail=f"{url} → {'ok' if ok else 'ko'}")


__all__ = [
    "GateContext",
    "GateOutcome",
    "evaluate",
    "gate",
    "gate_needs",
    "is_async_gate",
    "known_gates",
    "matches_any",
    "scan_secrets",
]
