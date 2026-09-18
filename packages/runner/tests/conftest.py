"""Fixtures du runner : dépôt jouet, StageInput, API interne simulée, agent ACP factice."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import (
    AgentRef,
    Budget,
    Callbacks,
    ContextPack,
    Finding,
    LaunchSpec,
    ModelRef,
    Permissions,
    PlaybookRef,
    ProjectRef,
    RepoRef,
    StageInput,
    StageResult,
    TransitionRef,
    WorkItemRef,
)

FAKE_AGENT = Path(__file__).parent / "fixtures" / "fake_agent.py"


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def toy_repo(tmp_path: Path) -> Path:
    """Dépôt jouet local : un Makefile dont `make test` passe ou échoue sur commande."""
    origin = tmp_path / "origin"
    origin.mkdir()
    git("init", "--bare", "--initial-branch=main", cwd=origin)

    work = tmp_path / "seed"
    work.mkdir()
    git("init", "--initial-branch=main", cwd=work)
    git("config", "user.email", "seed@test", cwd=work)
    git("config", "user.name", "seed", cwd=work)
    (work / "Makefile").write_text(
        "test:\n\t@test ! -f BROKEN && echo '3 passed, 0 failed' || (echo '1 failed, 2 passed'; exit 1)\n"
        "lint:\n\t@echo lint ok\n"
        "typecheck:\n\t@echo typecheck ok\n",
        encoding="utf-8",
    )
    (work / "src").mkdir()
    (work / "src" / "orders.py").write_text("def total(lines):\n    return sum(lines)\n", encoding="utf-8")
    (work / "src" / "billing.py").write_text("RATE = 0.2\n", encoding="utf-8")
    git("add", "-A", cwd=work)
    git("commit", "-m", "init", cwd=work)
    git("remote", "add", "origin", str(origin), cwd=work)
    git("push", "-u", "origin", "main", cwd=work)
    return origin


@pytest.fixture
def stage_input(toy_repo: Path, tmp_path: Path) -> StageInput:
    return StageInput(
        run_id="run-test-1",
        attempt=1,
        project=ProjectRef(
            slug="demo",
            org="varga",
            test_command="make test",
            lint_command="make lint",
            typecheck_command="make typecheck",
        ),
        work_item=WorkItemRef(
            key="varga/demo#1",
            title="Corriger le total",
            body="Le total ignore les avoirs.",
            size="M",
            risk="low",
        ),
        transition=TransitionRef(id="t-implement", role="implement", **{"from": "ready"}, to="in_progress"),
        repo=RepoRef(url=str(toy_repo), base_branch="main", work_branch="choregos/1-total", clone_depth=10),
        agent=AgentRef(backend="openhands", launch=LaunchSpec(command=[sys.executable, str(FAKE_AGENT)])),
        model=ModelRef(
            litellm_model="platform/standard", base_url="http://litellm:4000", api_format="openai"
        ),
        gateway_key="sk-fake",
        budget=Budget(usd=5, max_turns=20, max_minutes=5),
        allowed_paths=["src/orders.py", "src/orders/**", "tests/**"],
        playbook=PlaybookRef(ref="implement@test", prompt="Tu implémentes la correction."),
        permissions=Permissions(
            write_paths=["src/orders.py", "src/orders/**", "tests/**"],
            deny_commands=["kubectl", "terraform apply"],
            allow_domains=["github.com"],
            dod_iterations=2,
            max_findings=3,
        ),
        callbacks=Callbacks(api_url="http://localhost:0/internal", run_token="tok"),
    )


class FakeInternalClient:
    """API interne en mémoire : enregistre ce que le runner publie."""

    def __init__(self, stage_input: StageInput, *, context: ContextPack | None = None) -> None:
        self.stage_input = stage_input
        self.context = context or ContextPack.empty(stage_input.work_item.title)
        self.events: list[dict[str, Any]] = []
        self.results: list[StageResult] = []
        self.findings: list[Finding] = []
        self.questions: list[dict[str, Any]] = []
        self.scope_requests: list[dict[str, Any]] = []
        self.scope_decision = {"decision": "granted", "allowed_paths": []}
        self.ticket = {"key": stage_input.work_item.key, "title": stage_input.work_item.title, "body": ""}
        self.ci_logs = ""

    async def fetch_input(self) -> StageInput:
        return self.stage_input

    async def post_events(self, events: list[dict[str, Any]]) -> int:
        self.events.extend(events)
        return len(events)

    async def post_result(self, result: StageResult) -> dict[str, Any]:
        self.results.append(result)
        return {"status": "recorded"}

    async def report_finding(self, finding: Finding) -> dict[str, Any]:
        self.findings.append(finding)
        return {"accepted": True, "finding_id": f"f{len(self.findings)}", "remaining": 2}

    async def request_scope_change(self, paths: list[str], justification: str) -> dict[str, Any]:
        self.scope_requests.append({"paths": paths, "justification": justification})
        return self.scope_decision

    async def ask_human(self, text: str, options: list[str] | None = None) -> dict[str, Any]:
        self.questions.append({"text": text, "options": options or []})
        return {"status": "accepted"}

    async def fetch_context(self) -> ContextPack:
        return self.context

    async def fetch_ticket(self) -> dict[str, Any]:
        return self.ticket

    async def fetch_ci_logs(self, tail: int = 500) -> str:
        return self.ci_logs

    async def aclose(self) -> None:
        return None


def agent_script(script: dict[str, Any], stage_input: StageInput, workspace: Path) -> None:
    """Injecte le scénario de l'agent factice dans son environnement de lancement."""
    stage_input.agent.launch.env.update(
        {
            "FAKE_AGENT_SCRIPT": json.dumps(script, ensure_ascii=False),
            "CHOREGOS_WORKSPACE_PATH": str(workspace),
        }
    )


def valid_result(summary: str = "correction appliquée", **kwargs: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema": "choregos/StageResult/v1",
        "status": "done",
        "summary": summary,
        "evidence": {"tests_passed": True, "tests_run": 3},
    }
    payload.update(kwargs)
    return payload


@pytest.fixture
def runner_settings(tmp_path: Path) -> Any:
    from choregos_runner.config import RunnerSettings

    os.environ.setdefault("CHOREGOS_RUNNER_DRY_RUN", "0")
    return RunnerSettings(
        run_id="run-test-1",
        api_url="http://localhost:0/internal",
        run_token="tok",
        workspace=tmp_path / "workspace",
        dry_run=False,
    )
