# SPDX-License-Identifier: Apache-2.0
"""The policy-ceiling form's placeholders are the platform's defaults (S42-08).

`PLATFORM_DEFAULTS` in `apps/web/src/lib/policy-ceiling-form.ts` is what the form shows as the grey
figure of an empty field. It is hand-written TypeScript; this test reads it back and compares it with
the policy schema's defaults and the engine's constants, so a changed default turns the suite red
instead of leaving the console telling an administrator a wrong figure.
"""

from __future__ import annotations

import json
import pathlib
import re

from choregos_core.policy import (
    DEFAULT_MAX_MINUTES,
    DEFAULT_MAX_TURNS,
    DEFAULT_STAGE_USD,
    DEFAULT_TICKET_USD,
)

ROOT = pathlib.Path(__file__).resolve().parents[2]
FORM = ROOT / "apps" / "web" / "src" / "lib" / "policy-ceiling-form.ts"
SCHEMA = ROOT / "packages" / "contracts" / "schemas" / "policy.schema.json"


def _web_defaults() -> dict[str, object]:
    text = FORM.read_text(encoding="utf-8")
    block = re.search(r"PLATFORM_DEFAULTS = \{(.*?)\n\} as const;", text, re.S)
    assert block, "PLATFORM_DEFAULTS is gone or changed shape: update this test with it"
    found: dict[str, object] = {}
    for key, value in re.findall(r"^\s+(\w+): (\{[^}]*\}|[\d.]+),", block.group(1), re.M):
        if value.startswith("{"):
            found[key] = {k: float(v) for k, v in re.findall(r"(\w+): ([\d.]+)", value)}
        else:
            found[key] = float(value)
    return found


def test_the_form_placeholders_are_the_platform_defaults() -> None:
    web = _web_defaults()
    props = json.loads(SCHEMA.read_text(encoding="utf-8"))["properties"]
    assert web == {
        "ticket_usd": DEFAULT_TICKET_USD,
        "stage_usd": DEFAULT_STAGE_USD,
        "max_turns": float(DEFAULT_MAX_TURNS),
        "max_minutes": float(DEFAULT_MAX_MINUTES),
        "attempts_default_max": float(props["attempts"]["properties"]["default_max"]["default"]),
        "dod_iterations": float(props["attempts"]["properties"]["dod_iterations"]["default"]),
        "auto_grant_max_files": float(props["scope"]["properties"]["auto_grant_max_files"]["default"]),
        "max_diff_files": float(props["scope"]["properties"]["max_diff_files"]["default"]),
        "max_diff_lines": float(props["scope"]["properties"]["max_diff_lines"]["default"]),
        "findings_max_per_run": float(props["findings"]["properties"]["max_per_run"]["default"]),
    }


def test_the_form_minimums_are_the_ceiling_models() -> None:
    """Every field the form offers exists in the ceiling model, with the same minimum."""
    from choregos_core.policy_ceiling import PolicyCeiling

    text = FORM.read_text(encoding="utf-8")
    model = PolicyCeiling.model_json_schema()["$defs"]
    pairs = {
        ("CeilingBudgets", "tool_calls_per_run"): 1,
        ("CeilingAttempts", "default_max"): 1,
        ("CeilingAttempts", "dod_iterations"): 0,
        ("CeilingScope", "auto_grant_max_files"): 0,
        ("CeilingScope", "max_diff_files"): 1,
        ("CeilingScope", "max_diff_lines"): 1,
        ("CeilingFindings", "max_per_run"): 0,
    }
    for (cls, name), minimum in pairs.items():
        branch = next(b for b in model[cls]["properties"][name]["anyOf"] if b.get("type") == "integer")
        assert branch["minimum"] == minimum, (cls, name)
        spec = re.search(rf'path: \["\w+", "{name}"\], kind: "int", min: (\d+)', text)
        assert spec and int(spec.group(1)) == minimum, f"the form's minimum for {name} drifted"
    for name in ("daily_project_usd", "monthly_org_usd"):
        branch = next(
            b for b in model["CeilingBudgets"]["properties"][name]["anyOf"] if b.get("type") == "number"
        )
        assert branch["exclusiveMinimum"] == 0
        assert re.search(rf'"{name}"\], kind: "usd", min: 0, exclusive: true', text)
    # Every field the form writes is one the model accepts (extra="forbid").
    for section, cls in (
        ("budgets", "CeilingBudgets"),
        ("attempts", "CeilingAttempts"),
        ("scope", "CeilingScope"),
        ("findings", "CeilingFindings"),
    ):
        allowed = set(model[cls]["properties"])
        used = set(re.findall(rf'path: \["{section}", "(\w+)"', text))
        assert used <= allowed, (section, used - allowed)
