"""Policy engine : budgets, approbations, tentatives, périmètre.

Une politique répond à quatre questions, et à elles seules :
combien puis-je dépenser, qui doit approuver, combien de fois puis-je réessayer,
et où l'agent a-t-il le droit d'écrire.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from choregos_contracts import (
    ApprovalRule,
    Budget,
    HumanRequestKind,
    Policy,
    Risk,
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


@dataclass(slots=True, frozen=True)
class ApprovalDecision:
    """Faut-il un humain, et lequel ?"""

    required: bool
    group: str | None = None
    timeout_hours: int | None = None
    reason: str = ""


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

    # ───────────────────────── approbations ─────────────────────────

    def approval_for(
        self,
        kind: HumanRequestKind | str,
        size: Size | str | None = None,
        risk: Risk | str | None = None,
    ) -> ApprovalDecision:
        """Une approbation humaine est-elle requise pour ce type de décision ?"""
        attr = {
            "approval": "spec",
            "spec": "spec",
            "merge": "merge",
            "prod": "prod",
            "scope_change": "scope_change",
        }.get(str(kind), str(kind))
        rule: ApprovalRule | None = getattr(self.policy.approvals, attr, None)
        if rule is None:
            return ApprovalDecision(False, reason=f"aucune règle `{attr}` : pas d'approbation")
        if rule.required == "always":
            return ApprovalDecision(True, rule.group, rule.timeout_hours, f"`{attr}` : toujours")
        if rule.required == "never":
            return ApprovalDecision(False, reason=f"`{attr}` : jamais")
        if rule.required == "by_size":
            needed = str(size or Size.M) in {str(s) for s in rule.sizes}
            return ApprovalDecision(
                needed, rule.group, rule.timeout_hours, f"`{attr}` : par taille ({size}) → {needed}"
            )
        needed = str(risk or Risk.LOW) in {str(r) for r in rule.risks}
        return ApprovalDecision(
            needed, rule.group, rule.timeout_hours, f"`{attr}` : par risque ({risk}) → {needed}"
        )

    def human_review_required(self, risk: Risk | str | None) -> bool:
        return str(risk or Risk.LOW) in {str(r) for r in self.policy.review.require_human_for_risk}

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
        raise ValidationError([Issue("yaml.syntax", str(exc))], subject="politique") from exc
    if not isinstance(raw, dict):
        raise ValidationError(
            [Issue("yaml.not_a_mapping", "la politique doit être un objet")], subject="politique"
        )
    try:
        return Policy.model_validate(raw)
    except PydanticValidationError as exc:
        issues = [
            Issue(f"schema.{e['type']}", e["msg"], ".".join(str(p) for p in e["loc"])) for e in exc.errors()
        ]
        raise ValidationError(issues, subject="politique") from exc


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
