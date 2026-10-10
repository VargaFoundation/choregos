# SPDX-License-Identifier: Apache-2.0
"""Policy engine : budgets, tentatives, périmètre, trains.

Une politique répond à trois questions, et à elles seules : combien puis-je dépenser,
combien de fois puis-je réessayer, et où l'agent a-t-il le droit d'écrire — plus les règles
des trains de livraison (`release_train`).

Elle ne dit PAS qui doit approuver un ticket : les humains se déclarent DANS le workflow (un
acteur humain sur une transition, `train.approval` — ADR 0041, ADR 0044). `approvals.*` et
`review.require_human_for_risk` restent acceptés par le schéma (une politique existante reste
valide), mais personne ne les lit : `policy_warnings` le dit à qui les renseigne.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from choregos_contracts import (
    Budget,
    Policy,
    Size,
)
from pydantic import ValidationError as PydanticValidationError

from .errors import Issue, ValidationError
from .gates import matches_any

PRESETS_DIR = Path(__file__).parent / "presets"
PRESET_NAMES = ("solo", "team", "regulated")

DEFAULT_TICKET_USD: dict[str, float] = {"S": 8.0, "M": 25.0, "L": 60.0, "XL": 120.0}
DEFAULT_STAGE_USD: dict[str, float] = {"S": 3.0, "M": 8.0, "L": 18.0, "XL": 30.0}
DEFAULT_MAX_TURNS = 80
DEFAULT_MAX_MINUTES = 60


class PolicyEngine:
    """Applique une `Policy` ; toutes les méthodes sont pures et testables."""

    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    # ───────────────────────── budgets ─────────────────────────

    def budget_ticket(self, size: Size | str | None) -> float:
        key = str(size or Size.M)
        table = self.policy.budgets.ticket_usd or DEFAULT_TICKET_USD
        return float(table.get(key, table.get("M", DEFAULT_TICKET_USD["M"])))

    def stage_usd(self, role: str, size: Size | str | None) -> float:
        key = str(size or Size.M)
        stage = self.policy.budgets.stage_usd
        for candidate in (role, "default"):
            table = stage.get(candidate)
            if table and key in table:
                return float(table[key])
            if table and "M" in table:
                return float(table["M"])
        return DEFAULT_STAGE_USD.get(key, DEFAULT_STAGE_USD["M"])

    def max_turns(self, role: str, factor: float = 1.0) -> int:
        table = self.policy.budgets.max_turns
        value = table.get(role, table.get("default", DEFAULT_MAX_TURNS))
        return max(1, round(value * factor))

    def max_minutes(self, role: str, factor: float = 1.0) -> int:
        table = self.policy.budgets.max_minutes
        value = table.get(role, table.get("default", DEFAULT_MAX_MINUTES))
        return max(1, round(value * factor))

    def budget_for(self, role: str, size: Size | str | None, *, turns_factor: float = 1.0) -> Budget:
        """Budget complet d'une étape : argent, tours, minutes."""
        return Budget(
            usd=self.stage_usd(role, size),
            max_turns=self.max_turns(role, turns_factor),
            max_minutes=self.max_minutes(role, turns_factor),
        )

    def over_ticket_budget(self, spent_usd: float, size: Size | str | None) -> bool:
        return spent_usd > self.budget_ticket(size)

    def should_alert(self, spent_usd: float, size: Size | str | None) -> bool:
        ratio = self.policy.budgets.alert_at_ratio
        return spent_usd >= self.budget_ticket(size) * ratio

    def daily_budget(self) -> float | None:
        return self.policy.budgets.daily_project_usd

    # ───────────────────────── relecture ─────────────────────────

    def cross_backend_review(self) -> bool:
        return self.policy.review.cross_backend

    # ───────────────────────── tentatives ─────────────────────────

    def attempts_for(self, role: str) -> int:
        return self.policy.attempts.by_role.get(role, self.policy.attempts.default_max)

    def dod_iterations(self) -> int:
        return self.policy.attempts.dod_iterations

    # ───────────────────────── périmètre ─────────────────────────

    def scope_auto_grant(self, paths: list[str]) -> bool:
        """Un élargissement de périmètre peut-il être accordé sans humain ?"""
        limit = self.policy.scope.auto_grant_max_files
        if len(paths) > limit:
            return False
        return not any(matches_any(p, self.policy.scope.deny_paths) for p in paths)

    def path_denied(self, path: str) -> bool:
        return matches_any(path, self.policy.scope.deny_paths)

    def deny_commands(self) -> list[str]:
        return list(self.policy.sandbox.deny_commands)

    def allow_domains(self) -> list[str]:
        return list(self.policy.sandbox.network.allow_domains)

    def max_diff(self) -> tuple[int, int]:
        return self.policy.scope.max_diff_files, self.policy.scope.max_diff_lines

    # ───────────────────────── findings & mémoire ─────────────────────────

    def max_findings_per_run(self) -> int:
        return self.policy.findings.max_per_run

    def max_tool_calls_per_run(self) -> int:
        """Appels d'outils du catalogue par run. 0 = aucun plafond.

        Le plafond n'est pas là pour brider l'agent mais pour borner la facture : un
        outil du catalogue coûte à chaque appel, et une boucle qui s'emballe coûte
        autant qu'elle tourne.
        """
        return self.policy.budgets.tool_calls_per_run or 0

    def dedupe_threshold(self) -> float:
        return self.policy.findings.dedupe_threshold

    def auto_agent_ready(self, size: Size | str | None) -> bool:
        return str(size or Size.M) in {str(s) for s in self.policy.findings.auto_agent_ready_sizes}

    def notify_finding(self, severity: str) -> bool:
        return severity in {str(s) for s in self.policy.findings.notify_severities}

    def memory_budget(self, role: str) -> int:
        table = self.policy.memory.context_budget_tokens
        return table.get(role, table.get("default", 2000)) if self.policy.memory.enabled else 0

    def memory_enabled(self) -> bool:
        return self.policy.memory.enabled

    # ───────────────────────── train ─────────────────────────

    def train(self, env: str) -> Any:
        return self.policy.release_train.get(env)

    def train_envs(self) -> list[str]:
        return list(self.policy.release_train)


def parse_policy(text: str) -> Policy:
    """Parse une politique YAML/JSON avec erreurs lisibles."""
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ValidationError([Issue("yaml.syntax", str(exc))], subject="policy") from exc
    if not isinstance(raw, dict):
        raise ValidationError([Issue("yaml.not_a_mapping", "the policy must be an object")], subject="policy")
    try:
        return Policy.model_validate(raw)
    except PydanticValidationError as exc:
        issues = [
            Issue(f"schema.{e['type']}", e["msg"], ".".join(str(p) for p in e["loc"])) for e in exc.errors()
        ]
        raise ValidationError(issues, subject="policy") from exc


#: Les règles d'approbation que le schéma accepte encore, et que personne n'applique.
CLES_D_APPROBATION = ("spec", "merge", "prod", "scope_change")


def policy_warnings(policy: Policy) -> list[Issue]:
    """Ce qu'une politique écrit et que la plateforme n'applique pas (ADR 0044).

    `approvals.*` et `review.require_human_for_risk` ont l'air de gardes ; aucune n'existe : les
    humains se déclarent dans le workflow. Une règle `required: never` ou une liste vide ne promet
    rien et ne se signale pas. On lit ce que le TEXTE a écrit (`model_fields_set`), pas les défauts
    du modèle : une politique qui ne dit rien de `review` ne reçoit pas d'avertissement pour le
    `[high]` que le contrat met par défaut.
    """
    issues: list[Issue] = []
    for cle in CLES_D_APPROBATION:
        regle = getattr(policy.approvals, cle)
        if regle is not None and regle.required != "never":
            issues.append(
                Issue(
                    "policy.approval_not_enforced",
                    f"`approvals.{cle}` is not enforced: Choregos asks no one for this approval. "
                    "Declare a human transition in the workflow (a human actor, or `train.approval`).",
                    f"approvals.{cle}",
                )
            )
    revue = policy.review
    if "require_human_for_risk" in revue.model_fields_set and revue.require_human_for_risk:
        issues.append(
            Issue(
                "policy.review_not_enforced",
                "`review.require_human_for_risk` is not enforced: Choregos asks no one for this review. "
                "Declare a human transition in the workflow.",
                "review.require_human_for_risk",
            )
        )
    return issues


@lru_cache(maxsize=8)
def load_preset(name: str) -> Policy:
    """Charge un preset livré (`solo`, `team`, `regulated`)."""
    path = PRESETS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"preset de politique inconnu : {name} (connus : {', '.join(PRESET_NAMES)})")
    return parse_policy(path.read_text(encoding="utf-8"))


def preset_yaml(name: str) -> str:
    return (PRESETS_DIR / f"{name}.yaml").read_text(encoding="utf-8")


def engine_for(policy: Policy | str) -> PolicyEngine:
    """Construit un moteur depuis une politique, un YAML ou un nom de preset."""
    if isinstance(policy, Policy):
        return PolicyEngine(policy)
    if policy.startswith("preset:"):
        return PolicyEngine(load_preset(policy.removeprefix("preset:")))
    return PolicyEngine(parse_policy(policy))
