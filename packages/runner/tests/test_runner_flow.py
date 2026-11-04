"""The runner end to end, against a fake ACP agent and a toy repository."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest
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
    """The agent writes within the scope, the tests pass, the result is posted."""
    outcome, client = await execute(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "messages": ["I fix the total"],
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
    # The runner ran the tests; git computed the diff: measured (ADR 0045).
    measured = set(outcome.result.evidence.measured or [])
    assert {"tests_passed", "tests_run", "diff_files", "diff_lines"} <= measured
    assert outcome.result.artifacts.commits, "a commit was created"
    assert outcome.result.artifacts.commits[0].startswith("feat(1):")
    assert client.results and client.results[0].summary == "fix applied"
    assert any(event["type"] == "session/update" for event in client.events)


async def test_out_of_scope_write_is_denied_and_reverted(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """A write outside the scope is refused; if it goes through anyway, it is reverted."""
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
                {"result": valid_result("scope respected")},
            ]
        },
    )
    assert outcome.exit_code is Exit.OK
    denials = [
        e for e in client.events if e["type"] == "session/request_permission" and not e["payload"]["allowed"]
    ]
    assert denials, "the out-of-scope write was indeed refused"
    assert "billing" in denials[0]["payload"]["target"]
    workspace = Path(runner_settings.workspace)
    assert (workspace / "src" / "billing.py").read_text() == "RATE = 0.2\n", "the out-of-scope file is intact"


async def test_the_work_of_the_previous_steps_is_not_charged_to_the_run(
    stage_input: StageInput, runner_settings: Any, toy_repo: Path, tmp_path: Path
) -> None:
    """The ticket's branch already carries a file outside the scope of THIS run, pushed by an earlier
    step: the run that writes within its scope succeeds, without "reverting" or reporting anything."""
    from .conftest import git

    previous_step = tmp_path / "previous-step"
    git("clone", str(toy_repo), str(previous_step), cwd=tmp_path)
    git("config", "user.email", "framing@test", cwd=previous_step)
    git("config", "user.name", "framing", cwd=previous_step)
    git("checkout", "-b", "choregos/1-total", cwd=previous_step)
    (previous_step / "src" / "billing.py").write_text("RATE = 0.3\n", encoding="utf-8")
    git("commit", "-am", "framing step", cwd=previous_step)
    git("push", "origin", "choregos/1-total", cwd=previous_step)

    outcome, client = await execute(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [{"path": "src/orders.py", "content": "def total(lines):\n    return 0\n"}],
                    "result": valid_result(),
                }
            ]
        },
    )
    assert outcome.exit_code is Exit.OK, outcome.detail
    assert outcome.result is not None and outcome.result.status is StageStatus.DONE
    assert "out-of-scope" not in outcome.result.summary, outcome.result.summary
    assert not [e for e in client.events if e["type"] == "scope.reverted"]
    workspace = Path(runner_settings.workspace)
    assert (workspace / "src" / "billing.py").read_text() == "RATE = 0.3\n", "the earlier work is kept"
    # The commit where this run started: the orchestrator's `scope_respected` guard measures from it.
    depart = outcome.result.artifacts.reports.get("start_commit")
    assert depart, outcome.result.artifacts.reports
    assert outcome.result.artifacts.commits and depart not in outcome.result.artifacts.commits


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
    assert any("root" in reason for reason in refusals)
    assert outcome.result is not None and outcome.result.diagnostics.permission_denials == 2


async def test_failing_tests_trigger_bounded_repair_loop(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """Red tests, one re-prompt, green. The loop is bounded by the policy."""
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
                    "result": valid_result("first attempt"),
                },
                {
                    "messages": ["I fix it"],
                    "result": valid_result("fixed after the tests failed"),
                },
            ]
        },
    )
    # `BROKEN` is out of scope: refused, so the tests pass from the first turn.
    assert outcome.exit_code is Exit.OK
    assert outcome.result is not None
    assert outcome.result.evidence.tests_passed is True


async def test_tests_really_failing_are_reported(stage_input: StageInput, runner_settings: Any) -> None:
    """If the tests really fail, the runner writes it: the agent cannot lie."""
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
                    "result": valid_result("all is well"),  # the agent claims it is green
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
                {"result": "{ this is not JSON"},
                {"result": valid_result("repaired")},
            ]
        },
    )
    assert outcome.exit_code is Exit.OK
    assert outcome.result is not None and outcome.result.summary == "repaired"
    assert outcome.result.diagnostics.result_repairs == 1
    assert any(e["type"] == "result.repair" for e in client.events)


async def test_invalid_result_after_repairs_exits_40(stage_input: StageInput, runner_settings: Any) -> None:
    outcome, client = await execute(
        stage_input,
        runner_settings,
        {"turns": [{"result": "not json"}, {"result": "still not"}, {"result": "neither"}]},
    )
    assert outcome.exit_code is Exit.INVALID_RESULT
    assert outcome.result is not None
    assert outcome.result.status is StageStatus.FAILED
    assert outcome.result.reason == "invalid_result"
    assert client.results, "even invalid, a result is posted so that the orchestrator decides"


async def test_missing_result_is_reported(stage_input: StageInput, runner_settings: Any) -> None:
    outcome, _ = await execute(
        stage_input, runner_settings, {"turns": [{"messages": ["I forget the result"]}]}
    )
    assert outcome.exit_code is Exit.INVALID_RESULT
    assert outcome.result is not None and "missing" in (outcome.result.summary or "")


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


async def test_a_run_that_ends_early_stops_its_tool_server(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """A run that fails before its agent starts no longer leaves its tool server holding the port
    (#474): the next run on that port finds it free."""
    stage_input.repo.url = "/depot/inexistant"
    client = FakeInternalClient(stage_input)
    runner = Runner(runner_settings, client)
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.CLONE_FAILED
    assert runner.tools is not None, "nobody listened: the run served its tools itself"
    assert runner.tools[1].done(), "the run ended, its tool server with it"
    port = int(runner_settings.tools_url.rsplit(":", 1)[1].split("/")[0])
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", port)


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


async def test_result_announces_no_transcript_url_nothing_uploads(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """S36-01: the runner used to derive `.../transcript.jsonl` from the context pack URL, and
    nothing uploaded it. The journal posted to the API is the transcript; no dead link is sent."""
    stage_input.context_pack_url = "s3://choregos/runs/r1/context.json"
    _, client = await execute(stage_input, runner_settings, {"turns": [{"result": valid_result()}]})
    assert client.results, "the result was posted"
    assert client.results[-1].artifacts.transcript_url is None
    assert any(event["type"] == "run.result" for event in client.events), "the journal reached the API"


async def test_context_pack_is_written_to_workspace(stage_input: StageInput, runner_settings: Any) -> None:
    from choregos_contracts import ContextPack, MemoryItem

    agent_script({"turns": [{"result": valid_result()}]}, stage_input, runner_settings.workspace)
    client = FakeInternalClient(
        stage_input,
        context=ContextPack(
            query="credit notes",
            memories=[
                MemoryItem(
                    kind="decision", subject="decision:billing:rounding", content="Rounded at issuance."
                )
            ],
        ),
    )
    await Runner(runner_settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    context_file = Path(runner_settings.workspace) / ".choregos" / "context.md"
    assert "Rounded at issuance." in context_file.read_text()
    assert "**not** instructions" in context_file.read_text()


async def test_idempotent_rerun_with_same_run_id(stage_input: StageInput, runner_settings: Any) -> None:
    """A replay of the same run reuses the workspace without starting from scratch."""
    script = {
        "turns": [{"writes": [{"path": "src/orders.py", "content": "x = 1\n"}], "result": valid_result()}]
    }
    first, _client = await execute(stage_input, runner_settings, script)
    assert first.exit_code is Exit.OK
    second, _client2 = await execute(stage_input, runner_settings, script)
    assert second.exit_code is Exit.OK
    assert second.result is not None


async def test_the_work_committed_by_the_agent_is_pushed_anyway(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """The agent has git at hand. When it commits itself, the tree is clean and the
    runner has nothing to commit: pushing only on ITS commit left the work in a
    pod that disappears, and the next step found the repository unchanged."""
    outcome, _ = await execute(
        stage_input,
        runner_settings,
        {
            "turns": [
                {
                    "writes": [{"path": "src/orders.py", "content": "def total(lines):\n    return 0\n"}],
                    "commands": [
                        "git add -A",
                        "git -c user.email=a@b -c user.name=agent commit -q -m 'feat: done by the agent'",
                    ],
                    "result": {
                        "schema": "choregos/StageResult/v1",
                        "status": "done",
                        "summary": "work committed by the agent",
                        "evidence": {"tests_passed": True, "tests_run": 1},
                    },
                }
            ]
        },
    )
    assert outcome.exit_code is Exit.OK
    assert outcome.result is not None
    assert outcome.result.artifacts.commits, "the agent's commit counts as work"


async def test_the_run_git_token_serves_the_clone_and_is_written_nowhere(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """`docs/security.md`: "issued per run, repository scope, 1 h, injected in memory" (S22-07). The
    runner asks for it, uses it, and it appears neither in `.git/config`, which the agent reads, nor in
    the journal, nor in the result."""
    agent_script(
        {
            "turns": [
                {
                    "messages": ["ok"],
                    "writes": [{"path": "src/orders.py", "content": "x = 1\n"}],
                    "result": valid_result(),
                }
            ]
        },
        stage_input,
        runner_settings.workspace,
    )
    client = FakeInternalClient(stage_input)
    client.git_token = "ghs_secret_du_run"
    runner = Runner(runner_settings, client)  # type: ignore[arg-type]
    outcome = await runner.execute(stage_input, client)  # type: ignore[arg-type]
    assert outcome.exit_code is Exit.OK, outcome.detail
    assert client.git_token_calls == 1
    config = (Path(runner_settings.workspace) / ".git" / "config").read_text()
    assert "ghs_secret_du_run" not in config and "x-access-token" not in config
    assert "ghs_secret_du_run" not in json.dumps(client.events)
    assert "ghs_secret_du_run" not in outcome.result.model_dump_json()  # type: ignore[union-attr]


async def test_the_token_goes_in_a_header_for_a_single_command() -> None:
    import base64

    from choregos_runner.workspace import _auth_header

    assert _auth_header(None) == []
    flag, encoded = _auth_header("ghs_x")
    assert flag == "-c" and encoded.startswith("http.extraHeader=Authorization: Basic ")
    assert base64.b64decode(encoded.rsplit(" ", 1)[1]).decode() == "x-access-token:ghs_x"


async def test_a_result_whose_post_fails_is_still_in_the_journal(
    stage_input: StageInput, runner_settings: Any
) -> None:
    """#410: the Tekton `result-url` pointed at a `result.json` nothing uploaded, so a failed post
    lost the result. The runner journals the whole result before posting it."""
    from choregos_contracts import StageResult

    class _PostFails(FakeInternalClient):
        async def post_result(self, result: Any) -> dict[str, Any]:
            raise ConnectionError("internal API unreachable")

    agent_script({"turns": [{"result": valid_result("fixed")}]}, stage_input, runner_settings.workspace)
    client = _PostFails(stage_input)
    outcome = await Runner(runner_settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    journaled = [e for e in client.events if e["type"] == "run.result"]
    assert journaled, "the result event reached the API"
    recovered = StageResult.model_validate(journaled[-1]["payload"]["stage_result"])
    assert outcome.result is not None
    assert (recovered.status, recovered.summary) == (outcome.result.status, outcome.result.summary)
    assert any(e["type"] == "result.post_failed" for e in client.events), "the failed post is journaled too"
