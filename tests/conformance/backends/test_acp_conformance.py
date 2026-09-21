"""Les sept vérifications de conformité d'un backend ACP (docs/plan/02 §2.2).

Un backend qui échoue est désactivé dans `platform/backends` jusqu'à correction : c'est
la seule façon de faire vivre un écosystème d'agents inégal sans casser la plateforme.
"""

from __future__ import annotations

import json
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from choregos_contracts import StageInput
from choregos_runner.acp import AcpClient, AgentUnreachableError
from choregos_runner.backends import BACKEND_BINARIES, get_backend, known_backends
from choregos_runner.result import load_result

pytestmark = pytest.mark.conformance

FAKE_AGENT = (
    Path(__file__).resolve().parents[3] / "packages" / "runner" / "tests" / "fixtures" / "fake_agent.py"
)
CHECKS = (
    "initialize",
    "mcp",
    "prompt_trivial",
    "permission_denied",
    "result_valid",
    "max_turns",
    "cost_at_gateway",
)


@dataclass
class ConformanceResult:
    backend: str
    passed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    skipped: str | None = None

    @property
    def ok(self) -> bool:
        return not self.failed and self.skipped is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "backend": self.backend,
            "passed": self.passed,
            "failed": self.failed,
            "skipped": self.skipped,
            "total": len(CHECKS),
        }


def backend_available(name: str) -> bool:
    """Le binaire du backend est-il installé dans cette image ?"""
    return shutil.which(BACKEND_BINARIES.get(name, name)) is not None


@pytest.fixture
def stage_input(tmp_path: Path) -> StageInput:
    from choregos_contracts import (
        AgentRef,
        Budget,
        Callbacks,
        LaunchSpec,
        ModelRef,
        Permissions,
        PlaybookRef,
        ProjectRef,
        RepoRef,
        TransitionRef,
        WorkItemRef,
    )

    return StageInput(
        run_id="conformance-1",
        attempt=1,
        project=ProjectRef(slug="conformance", org="varga"),
        work_item=WorkItemRef(key="varga/conformance#1", title="créer hello.py et son test"),
        transition=TransitionRef(id="t-implement", role="implement", **{"from": "ready"}, to="in_progress"),
        repo=RepoRef(url=str(tmp_path), base_branch="main", work_branch="choregos/1"),
        agent=AgentRef(backend="claude-code", launch=LaunchSpec(command=[sys.executable, str(FAKE_AGENT)])),
        model=ModelRef(
            litellm_model="platform/standard", base_url="http://localhost:4000", api_format="openai"
        ),
        gateway_key="sk-conformance",
        budget=Budget(usd=1, max_turns=3, max_minutes=2),
        allowed_paths=["hello.py", "test_hello.py"],
        playbook=PlaybookRef(ref="implement@conformance", prompt="crée hello.py et un test"),
        permissions=Permissions(write_paths=["hello.py", "test_hello.py"], max_findings=1),
        callbacks=Callbacks(api_url="http://localhost:0/internal", run_token="tok"),
    )


async def run_suite(backend_name: str, stage_input: StageInput, workspace: Path) -> ConformanceResult:
    """Exécute les sept vérifications contre un backend donné."""
    result = ConformanceResult(backend=backend_name)
    backend = get_backend(backend_name)
    stage_input.agent.backend = backend_name
    # Chaque backend reçoit un modèle qu'il accepte : la contrainte de famille de modèles
    # fait partie du contrat (claude-code n'accepte que Claude).
    if backend.model_constraint:
        stage_input.model.litellm_model = f"anthropic/{backend.model_constraint[0]}-sonnet-5"
    plan = backend.launch_plan(stage_input, workspace)
    plan.materialize(workspace)

    script = {
        "turns": [
            {
                "messages": ["je crée le fichier"],
                "writes": [
                    {"path": "hello.py", "content": "def hello():\n    return 'bonjour'\n"},
                    {"path": "interdit.py", "content": "x = 1\n"},
                ],
                "result": {
                    "schema": "choregos/StageResult/v1",
                    "status": "done",
                    "summary": "hello.py créé",
                    "evidence": {"tests_passed": True, "tests_run": 1},
                },
            }
        ]
    }
    env = {
        **plan.env,
        **backend.model_env(stage_input.model, stage_input.gateway_key),
        "FAKE_AGENT_SCRIPT": json.dumps(script),
        "CHOREGOS_WORKSPACE_PATH": str(workspace),
    }
    denials: list[str] = []

    async def on_permission(params: dict[str, Any]) -> tuple[bool, str]:
        target = str(((params.get("toolCall") or {}).get("rawInput") or {}).get("path", ""))
        if target not in stage_input.allowed_paths:
            denials.append(target)
            return False, "hors périmètre"
        return True, "autorisé"

    client = AcpClient(plan.command, cwd=str(workspace), env=env, on_permission=on_permission)
    try:
        async with client:
            # 1. initialize
            info = await client.initialize()
            if info:
                result.passed.append("initialize")
            else:
                result.failed["initialize"] = "réponse vide"
            # 2. serveurs MCP acceptés
            session = await client.new_session(str(workspace), plan.mcp_servers)
            if session:
                result.passed.append("mcp")
            else:
                result.failed["mcp"] = "session/new refusée"
            # 3. prompt trivial
            outcome = await client.prompt("crée hello.py", timeout=60)
            if (workspace / "hello.py").exists():
                result.passed.append("prompt_trivial")
            else:
                result.failed["prompt_trivial"] = "le fichier demandé n'a pas été créé"
            # 4. une permission refusée n'est pas contournée
            if denials and not (workspace / "interdit.py").exists():
                result.passed.append("permission_denied")
            else:
                result.failed["permission_denied"] = "l'écriture refusée a eu lieu quand même"
            # 5. résultat valide
            load = load_result(workspace / ".choregos" / "result.json")
            if load.ok:
                result.passed.append("result_valid")
            else:
                result.failed["result_valid"] = load.error or "résultat illisible"
            # 6. respect de max_turns
            if outcome.turns <= stage_input.budget.max_turns:
                result.passed.append("max_turns")
            else:
                result.failed["max_turns"] = f"{outcome.turns} tours > {stage_input.budget.max_turns}"
            # 7. le coût apparaît au gateway : vérifié par l'environnement du modèle
            model_env = backend.model_env(stage_input.model, stage_input.gateway_key)
            if any(stage_input.gateway_key in value for value in model_env.values()):
                result.passed.append("cost_at_gateway")
            else:
                result.failed["cost_at_gateway"] = "la clé du run n'est pas passée au backend"
    except AgentUnreachableError as exc:
        result.failed["initialize"] = str(exc)
    return result


async def test_default_backend_passes_all_checks(stage_input: StageInput, tmp_path: Path) -> None:
    """Claude Code (agent par défaut) doit passer les sept vérifications."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    result = await run_suite("claude-code", stage_input, workspace)
    assert result.ok, result.failed
    assert set(result.passed) == set(CHECKS)


@pytest.mark.parametrize("backend", [name for name in known_backends() if name != "claude-code"])
async def test_other_backends(backend: str, stage_input: StageInput, tmp_path: Path) -> None:
    """Les autres backends sont testés avec l'agent factice ; l'agent réel tourne la nuit."""
    workspace = tmp_path / backend
    workspace.mkdir()
    result = await run_suite(backend, stage_input, workspace)
    if not result.ok:
        pytest.fail(f"{backend} : {result.failed}")


async def test_protocol_covers_the_seven_checks() -> None:
    assert len(CHECKS) == 7


def test_backends_declare_their_binary() -> None:
    """Chaque backend connu sait quel binaire l'exécute (pour l'image et les versions)."""
    for name in known_backends():
        assert name in BACKEND_BINARIES, name
