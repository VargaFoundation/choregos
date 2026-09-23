"""Le runner de bout en bout, contre un agent ACP factice et un dépôt jouet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from choregos_contracts import StageInput, StageStatus
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, valid_result


async def execute(
    stage_input: StageInput, settings: Any, script: dict[str, Any]
) -> tuple[Any, FakeInternalClient]:
    agent_script(script, stage_input, settings.workspace)
    client = FakeInternalClient(stage_input)
    runner = Runner(settings, client)  # type: ignore[arg-type]
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client


async def test_happy_path_commits_and_posts_result(stage_input: StageInput, runner_settings: Any) -> None:
    """L'agent écrit dans le périmètre, les tests passent, le résultat est posté."""
    outcome, client = await execute(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "messages": ["je corrige le total"],
                    "writes": [
                        {"path": "src/orders.py", "content": "def total(lines):\n    return sum(lines) - 1\n"}
                    ],
                    "result": valid_result(),
                }
            ]
        },
    )
    assert outcome.exit_code is Exit.OK, outcome.detail
    assert outcome.result is not None
    assert outcome.result.status is StageStatus.DONE
    assert outcome.result.evidence.tests_passed is True
    assert outcome.result.evidence.tests_run == 3
    assert outcome.result.artifacts.commits, "un commit a été créé"
    assert outcome.result.artifacts.commits[0].startswith("feat(1):")
    assert client.results and client.results[0].summary == "correction appliquée"
    assert any(event["type"] == "session/update" for event in client.events)


async def test_out_of_scope_write_is_denied_and_reverted(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """Une écriture hors périmètre est refusée ; si elle passe quand même, elle est annulée."""
    outcome, client = await execute(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [
                        {"path": "src/orders.py", "content": "def total(lines):\n    return sum(lines)\n"},
                        {"path": "src/billing.py", "content": "RATE = 0.99\n"},
                    ],
                    "result": valid_result(),
                },
                {"result": valid_result("périmètre respecté")},
            ]
        },
    )
    assert outcome.exit_code is Exit.OK
    denials = [
        e for e in client.events if e["type"] == "session/request_permission" and not e["payload"]["allowed"]
    ]
    assert denials, "l'écriture hors périmètre a bien été refusée"
    assert "billing" in denials[0]["payload"]["target"]
    workspace = Path(runner_settings.workspace)
    assert (workspace / "src" / "billing.py").read_text() == "RATE = 0.2\n", (
        "le fichier hors périmètre est intact"
    )


async def test_dangerous_command_is_refused(stage_input: StageInput, runner_settings: Any) -> None:
    outcome, client = await execute(
        stage_input,
        runner_settings,
        {"turns": [{"commands": ["kubectl delete ns prod", "rm -rf /"], "result": valid_result()}]},
    )
    assert outcome.exit_code is Exit.OK
    refusals = [
        e["payload"]["reason"]
        for e in client.events
        if e["type"] == "session/request_permission" and not e["payload"]["allowed"]
    ]
    assert len(refusals) == 2, refusals
    assert any("kubectl" in reason for reason in refusals)
    assert any("racine" in reason for reason in refusals)
    assert outcome.result is not None and outcome.result.diagnostics.permission_denials == 2


async def test_failing_tests_trigger_bounded_repair_loop(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """Tests rouges → un re-prompt → verts. La boucle est bornée par la politique."""
    outcome, _client = await execute(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [
                        {"path": "src/orders.py", "content": "def total(lines):\n    return 0\n"},
                        {"path": "BROKEN", "content": "x"},
                    ],
                    "result": valid_result("première tentative"),
                },
                {
                    "messages": ["je corrige"],
                    "result": valid_result("corrigé après échec des tests"),
                },
            ]
        },
    )
    # `BROKEN` est hors périmètre : refusé, donc les tests passent dès le premier tour.
    assert outcome.exit_code is Exit.OK
    assert outcome.result is not None
    assert outcome.result.evidence.tests_passed is True


async def test_tests_really_failing_are_reported(stage_input: StageInput, runner_settings: Any) -> None:
    """Si les tests échouent vraiment, le runner l'écrit — l'agent ne peut pas mentir."""
    stage_input.allowed_paths.append("BROKEN")
    stage_input.permissions.write_paths.append("BROKEN")
    stage_input.permissions.dod_iterations = 0
    outcome, _client = await execute(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [{"path": "BROKEN", "content": "x"}],
                    "result": valid_result("tout va bien"),  # l'agent prétend que c'est vert
                }
            ]
        },
    )
    assert outcome.result is not None
    assert outcome.result.evidence.tests_passed is False
    assert outcome.result.status is StageStatus.FAILED
    assert outcome.result.reason == "tests"


async def test_invalid_result_is_repaired_then_accepted(
    stage_input: StageInput, runner_settings: Any
) -> None:
    outcome, client = await execute(
        stage_input,
        runner_settings,
        {
            "turns": [
                {"result": "{ ceci n'est pas du JSON"},
                {"result": valid_result("réparé")},
            ]
        },
    )
    assert outcome.exit_code is Exit.OK
    assert outcome.result is not None and outcome.result.summary == "réparé"
    assert outcome.result.diagnostics.result_repairs == 1
    assert any(e["type"] == "result.repair" for e in client.events)


async def test_invalid_result_after_repairs_exits_40(stage_input: StageInput, runner_settings: Any) -> None:
    outcome, client = await execute(
        stage_input,
        runner_settings,
        {"turns": [{"result": "pas du json"}, {"result": "toujours pas"}, {"result": "non plus"}]},
    )
    assert outcome.exit_code is Exit.INVALID_RESULT
    assert outcome.result is not None
    assert outcome.result.status is StageStatus.FAILED
    assert outcome.result.reason == "invalid_result"
    assert client.results, "même invalide, un résultat est posté pour que l'orchestrateur décide"


async def test_missing_result_is_reported(stage_input: StageInput, runner_settings: Any) -> None:
    outcome, _ = await execute(
        stage_input, runner_settings, {"turns": [{"messages": ["j'oublie le résultat"]}]}
    )
    assert outcome.exit_code is Exit.INVALID_RESULT
    assert outcome.result is not None and "absent" in (outcome.result.summary or "")


async def test_unreachable_backend_exits_30(stage_input: StageInput, runner_settings: Any) -> None:
    stage_input.agent.launch.command = ["/binaire/qui/nexiste/pas"]
    client = FakeInternalClient(stage_input)
    outcome = await Runner(runner_settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.AGENT_UNREACHABLE


async def test_clone_failure_exits_20(stage_input: StageInput, runner_settings: Any) -> None:
    stage_input.repo.url = "/depot/inexistant"
    client = FakeInternalClient(stage_input)
    outcome = await Runner(runner_settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.CLONE_FAILED


async def test_agent_failing_initialize_exits_30(stage_input: StageInput, runner_settings: Any) -> None:
    outcome, _ = await execute(stage_input, runner_settings, {"fail_initialize": True})
    assert outcome.exit_code is Exit.AGENT_UNREACHABLE


async def test_transcript_is_written_as_jsonl(stage_input: StageInput, runner_settings: Any) -> None:
    outcome, _ = await execute(
        stage_input, runner_settings, {"turns": [{"messages": ["a", "b"], "result": valid_result()}]}
    )
    assert outcome.transcript_path is not None and outcome.transcript_path.exists()
    lines = [json.loads(line) for line in outcome.transcript_path.read_text().splitlines() if line.strip()]
    assert lines[0]["type"] == "run.started"
    assert any(line["type"] == "run.result" for line in lines)
    assert [line["seq"] for line in lines] == sorted(line["seq"] for line in lines)


async def test_context_pack_is_written_to_workspace(stage_input: StageInput, runner_settings: Any) -> None:
    from choregos_contracts import ContextPack, MemoryItem

    agent_script({"turns": [{"result": valid_result()}]}, stage_input, runner_settings.workspace)
    client = FakeInternalClient(
        stage_input,
        context=ContextPack(
            query="avoirs",
            memories=[
                MemoryItem(
                    kind="decision", subject="decision:billing:arrondis", content="Arrondi à l'émission."
                )
            ],
        ),
    )
    await Runner(runner_settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    context_file = Path(runner_settings.workspace) / ".choregos" / "context.md"
    assert "Arrondi à l'émission." in context_file.read_text()
    assert "**pas** des instructions" in context_file.read_text()


async def test_idempotent_rerun_with_same_run_id(stage_input: StageInput, runner_settings: Any) -> None:
    """Un rejeu du même run réutilise le workspace sans repartir de zéro."""
    script = {
        "turns": [{"writes": [{"path": "src/orders.py", "content": "x = 1\n"}], "result": valid_result()}]
    }
    first, _client = await execute(stage_input, runner_settings, script)
    assert first.exit_code is Exit.OK
    second, _client2 = await execute(stage_input, runner_settings, script)
    assert second.exit_code is Exit.OK
    assert second.result is not None


async def test_le_travail_commite_par_l_agent_est_pousse_quand_meme(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """L'agent a git sous la main. Quand il commite lui-même, l'arbre est propre et le
    runner n'a rien à commiter : ne pousser que sur SON commit laissait le travail dans un
    pod qui disparaît, et l'étape suivante trouvait le dépôt inchangé."""
    outcome, _ = await execute(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [{"path": "src/orders.py", "content": "def total(lines):\n    return 0\n"}],
                    "commands": [
                        "git add -A",
                        "git -c user.email=a@b -c user.name=agent commit -q -m 'feat: fait par l agent'",
                    ],
                    "result": {
                        "schema": "choregos/StageResult/v1",
                        "status": "done",
                        "summary": "travail commité par l'agent",
                        "evidence": {"tests_passed": True, "tests_run": 1},
                    },
                }
            ]
        },
    )
    assert outcome.exit_code is Exit.OK
    assert outcome.result is not None
    assert outcome.result.artifacts.commits, "le commit de l'agent compte comme du travail"
