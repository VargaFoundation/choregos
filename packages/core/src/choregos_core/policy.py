# SPDX-License-Identifier: Apache-2.0
"""Policy engine: budgets, attempts, scope, trains.

A policy answers three questions, and only those: how much may I spend, how many times may I
retry, and where may the agent write; plus the rules of release trains (`release_train`).

It does NOT say who must approve a work item: people are declared IN the workflow (a human actor
on a transition, `train.approval`; ADR 0041, ADR 0044). The keys `approvals.*`,
`review.require_human_for_risk` and `attempts.default_max` stay accepted by the schema (a stored
policy still loads), but nobody reads them: they are refused when a policy is written or a project
is created from a template (`unenforced_governance`, ADR 0101).
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
from .network import validate_pattern

PRESETS_DIR = Path(__file__).parent / "presets"
PRESET_NAMES = ("solo", "team", "regulated")

DEFAULT_TICKET_USD: dict[str, float] = {"S": 8.0, "M": 25.0, "L": 60.0, "XL": 120.0}
DEFAULT_STAGE_USD: dict[str, float] = {"S": 3.0, "M": 8.0, "L": 18.0, "XL": 30.0}
DEFAULT_MAX_TURNS = 80
DEFAULT_MAX_MINUTES = 60


class PolicyEngine:
    """Apply a `Policy`; all the methods are pure and testable."""

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
        """The full budget of a stage: money, turns, minutes."""
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

    # ───────────────────────── review ─────────────────────────

    def cross_backend_review(self) -> bool:
        return self.policy.review.cross_backend

    # ───────────────────────── tentatives ─────────────────────────

    def attempts_for(self, role: str) -> int:
        """The most attempts an agent transition of this role gets: a ceiling on the workflow's
        `max_attempts` (S27-08)."""
        return self.policy.attempts.by_role.get(role, self.policy.attempts.default_max)

    def attempts_ceilings(self) -> dict[str, int]:
        """The ceiling on an agent transition's `max_attempts`, for each role `by_role` names
        (S27-08). `default_max` is not one: applied to every role, it would silently cut the
        attempts the shipped workflows declare; `policy_warnings` says so to whoever writes it."""
        return dict(self.policy.attempts.by_role)

    def max_stages_per_ticket(self) -> int:
        return self.policy.attempts.max_stages_per_ticket

    def dod_iterations(self) -> int:
        return self.policy.attempts.dod_iterations

    # ───────────────────────── perimeter ─────────────────────────

    def scope_auto_grant(self, paths: list[str]) -> bool:
        """Can a perimeter enlargement be granted without a human?"""
        limit = self.policy.scope.auto_grant_max_files
        if len(paths) > limit:
            return False
        return not any(matches_any(p, self.policy.scope.deny_paths) for p in paths)

    def path_denied(self, path: str) -> bool:
        return matches_any(path, self.policy.scope.deny_paths)

    def deny_paths(self) -> list[str]:
        """The paths no run may write (`scope.deny_paths`): the runner refuses them and
        `scope_respected` fails on them, whatever the work item's allowed paths say (C2)."""
        return list(self.policy.scope.deny_paths)

    def deny_commands(self) -> list[str]:
        return list(self.policy.sandbox.deny_commands)

    def allow_domains(self) -> list[str]:
        return list(self.policy.sandbox.network.allow_domains)

    def max_diff(self) -> tuple[int, int]:
        return self.policy.scope.max_diff_files, self.policy.scope.max_diff_lines

    # ───────────────────────── findings & memory ─────────────────────────

    def max_findings_per_run(self) -> int:
        return self.policy.findings.max_per_run

    def max_tool_calls_per_run(self) -> int:
        """Catalogue tool calls per run. 0 = no ceiling.

        The ceiling is not there to rein in the agent but to bound the bill: a
        catalogue tool costs on every call, and a runaway loop costs
        as much as it runs.
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
    """Parse a YAML/JSON policy with readable errors."""
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


#: The approval rules the schema still accepts, and that nobody applies.
APPROVAL_KEYS = ("spec", "merge", "prod", "scope_change")


def policy_warnings(policy: Policy) -> list[Issue]:
    """What a policy writes and the platform does not apply (ADR 0044).

    `approvals.*` and `review.require_human_for_risk` look like guards; none exists: humans
    are declared in the workflow. A `required: never` rule or an empty list promises
    nothing and is not reported. What the TEXT wrote is read (`model_fields_set`), not the model's
    defaults: a policy that says nothing about `review` gets no warning for the
    `[high]` the contract sets by default.
    """
    issues: list[Issue] = []
    for key in APPROVAL_KEYS:
        rule = getattr(policy.approvals, key)
        if rule is not None and rule.required != "never":
            issues.append(
                Issue(
                    "policy.approval_not_enforced",
                    f"`approvals.{key}` is not enforced: Choregos asks no one for this approval. "
                    "Declare a human transition in the workflow (a human actor, or `train.approval`).",
                    f"approvals.{key}",
                )
            )
    if "default_max" in policy.attempts.model_fields_set:
        issues.append(
            Issue(
                "policy.default_max_not_enforced",
                "`attempts.default_max` is not enforced: a transition's attempts are its workflow's "
                "`max_attempts`, lowered only by `attempts.by_role` for the roles it names.",
                "attempts.default_max",
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
    net = policy.sandbox.network
    if "deny_by_default" in net.model_fields_set and not net.deny_by_default:
        issues.append(
            Issue(
                "policy.network_deny_by_default_not_enforced",
                "`sandbox.network.deny_by_default: false` is not enforced: egress is always closed. "
                "Write `allow_domains: ['*']` to open every public host.",
                "sandbox.network.deny_by_default",
            )
        )
    for name in ("allow_domains", "deny_domains"):
        for entry in getattr(net, name):
            try:
                validate_pattern(entry)
            except ValueError as error:
                issues.append(
                    Issue(
                        "policy.network_pattern_ignored",
                        f"`sandbox.network.{name}`: {error}; the entry is ignored.",
                        f"sandbox.network.{name}",
                    )
                )
    return issues


#: The warning codes that are refused when written: guards the platform accepts and never applies.
UNENFORCED_GOVERNANCE_CODES = frozenset(
    {"policy.approval_not_enforced", "policy.default_max_not_enforced", "policy.review_not_enforced"}
)


def unenforced_governance(policy: Policy) -> list[Issue]:
    """The `policy_warnings` issues that promise a guard nobody applies (ADR 0101)."""
    return [issue for issue in policy_warnings(policy) if issue.code in UNENFORCED_GOVERNANCE_CODES]


@lru_cache(maxsize=8)
def load_preset(name: str) -> Policy:
    """Load a shipped preset (`solo`, `team`, `regulated`)."""
    path = PRESETS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"unknown policy preset: {name} (known: {', '.join(PRESET_NAMES)})")
    return parse_policy(path.read_text(encoding="utf-8"))


def preset_yaml(name: str) -> str:
    return (PRESETS_DIR / f"{name}.yaml").read_text(encoding="utf-8")


def engine_for(policy: Policy | str) -> PolicyEngine:
    """Build an engine from a policy, a YAML or a preset name."""
    if isinstance(policy, Policy):
        return PolicyEngine(policy)
    if policy.startswith("preset:"):
        return PolicyEngine(load_preset(policy.removeprefix("preset:")))
    return PolicyEngine(parse_policy(policy))
