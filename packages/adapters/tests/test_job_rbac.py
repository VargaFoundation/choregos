"""Does the chart's Role cover what the executor really does?

Twice in one day, a missing verb killed a ticket: `patch` on the Jobs
(waking a queued Job) then `update` on the Secrets (replacing a token). Each
time the API answered 403, the exception came up, and the workflow died, on a message
that talks about rights, never about what was being attempted.

This test reads both sources and confronts them: the HTTP calls the executor writes, and
the verbs the chart grants. A review did not see it twice; it will not see it
the third time.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
EXECUTOR = ROOT / "packages/adapters/src/choregos_adapters/executor/k8s_job.py"
ROLE = ROOT / "charts/choregos/charts/orchestrator/templates/rbac-jobs.yaml"

#: What the HTTP verb requires as an RBAC verb. `GET` covers `get` and `list`; the
#: more precise one, `get`, is required, because it is the one a named call consumes.
VERBS = {"GET": "get", "POST": "create", "PUT": "update", "PATCH": "patch", "DELETE": "delete"}

CALL = re.compile(r'"(GET|POST|PUT|PATCH|DELETE)",\s*\n?\s*f?"[^"]*/(jobs|secrets)')


def _uses() -> set[tuple[str, str]]:
    source = EXECUTOR.read_text(encoding="utf-8")
    return {(resource, VERBS[method]) for method, resource in CALL.findall(source)}


def _granted() -> dict[str, set[str]]:
    # The template is Helm: only the `rules` block is kept, which is pure YAML.
    text = ROLE.read_text(encoding="utf-8")
    start = text.index("rules:")
    end = text.index("---", start)
    # Helm control lines (`{{- if ... }}`) are dropped: every rule is read, whichever
    # executor turns it on.
    block = "\n".join(line for line in text[start:end].splitlines() if not line.strip().startswith("{{"))
    rules = yaml.safe_load(block)["rules"]
    # Several rules may grant verbs on the same resource: they add up.
    granted: dict[str, set[str]] = {}
    for rule in rules:
        for resource in rule["resources"]:
            granted.setdefault(resource, set()).update(rule["verbs"])
    return granted


def test_there_are_calls_to_confront() -> None:
    """Without this guard, renaming a method would make the test green and empty."""
    uses = _uses()
    assert {"jobs", "secrets"} <= {r for r, _ in uses}, uses


@pytest.mark.parametrize(("resource", "verb"), sorted(_uses()))
def test_the_role_grants_what_the_executor_uses(resource: str, verb: str) -> None:
    granted = _granted().get(resource, set())
    assert verb in granted, (
        f"the executor does `{verb}` on `{resource}`, the Role does not grant it "
        f"(granted: {sorted(granted)}). The API will answer 403 and the ticket will die."
    )
