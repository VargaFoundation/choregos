"""Does the Role of a project namespace grant what the executor really does?

`packages/adapters/tests/test_rbac_du_job.py` checks the executor against the **chart**'s Role,
the one of the platform namespace, where the bench runs. A provisioned project gets another
Role, rendered by `gitops.py` in ITS runners namespace, and that one had neither `patch` on Jobs
nor `update`/`patch` on Secrets: the admission queue would have failed there silently (a failed
admission is silent, by choice) and a replayed run would have left with a stale token. Same
method, same requirement, on the second source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml
from choregos_contracts import ProjectConfig, RepoConfig
from choregos_core import load_preset
from choregos_orchestrator.gitops import render_project_manifests

ROOT = Path(__file__).resolve().parents[3]
EXECUTOR = ROOT / "packages/adapters/src/choregos_adapters/executor/k8s_job.py"
VERB = {"GET": "get", "POST": "create", "PUT": "update", "PATCH": "patch", "DELETE": "delete"}
CALL = re.compile(r'"(GET|POST|PUT|PATCH|DELETE)",\s*\n?\s*f?"[^"]*/(jobs|secrets)')


def _uses() -> set[tuple[str, str]]:
    source = EXECUTOR.read_text(encoding="utf-8")
    return {(resource, VERB[method]) for method, resource in CALL.findall(source)}


def _project_role() -> dict[str, set[str]]:
    config = ProjectConfig(
        slug="billing", org="varga", repo=RepoConfig(url="https://github.com/varga/billing.git")
    )
    files = render_project_manifests("billing", config, load_preset("team"))
    roles = [
        doc
        for text in files.values()
        for doc in yaml.safe_load_all(text)
        if doc and doc.get("kind") == "Role" and doc["metadata"]["name"] == "choregos-executor"
    ]
    assert len(roles) == 1, "one `choregos-executor` Role per project"
    return {resource: set(rule["verbs"]) for rule in roles[0]["rules"] for resource in rule["resources"]}


def test_there_are_calls_to_check() -> None:
    assert {"jobs", "secrets"} <= {r for r, _ in _uses()}


@pytest.mark.parametrize(("resource", "verb"), sorted(_uses()))
def test_the_project_role_grants_what_the_executor_uses(resource: str, verb: str) -> None:
    granted = _project_role().get(resource, set())
    assert verb in granted, (
        f"the executor does `{verb}` on `{resource}`, the project Role does not grant it "
        f"(granted: {sorted(granted)}). In a project namespace, the API would answer 403."
    )
