"""A refusal shim's refusal reaches the run's journal, and the shim holds no credential (#1346).

The runner image puts `choregos-refuse` (`docker/shims/refuse.sh`) in the place of `kubectl`, `helm`
and the other tools an agent has no reason to call. The shim used to post `shim.refused` with
`$CHOREGOS_RUN_TOKEN`, which the agent no longer holds (ADR 0115, S57-02): the event was lost. The
runner now reads the shim's line in the call's output, which it journals anyway, and journals
`shim.refused` after it. Here the real shim runs from the fake agent's shell, under the runner.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from choregos_contracts import StageInput
from choregos_runner.exits import Exit
from choregos_runner.refusal_shims import EVENT, SHIMMED, ShimRefusals

from .test_runner_flow import execute, valid_result

ROOT = Path(__file__).resolve().parents[3]
SHIM = ROOT / "docker" / "shims" / "refuse.sh"
DOCKERFILE = ROOT / "docker" / "runner.Dockerfile"


def _shimmed(bin_dir: Path, tool: str) -> Path:
    """`tool` as the runner image installs it: a link to the shim, which reads its name from `$0`."""
    bin_dir.mkdir(exist_ok=True)
    link = bin_dir / tool
    link.symlink_to(SHIM)
    return link


async def test_a_refused_tool_reaches_the_runs_journal(
    stage_input: StageInput, runner_settings: Any, tmp_path: Path
) -> None:
    kubectl = _shimmed(tmp_path / "bin", "kubectl")
    outcome, client = await execute(
        stage_input,
        runner_settings,
        {"turns": [{"shell": [[str(kubectl), "get", "secrets"]], "result": valid_result()}]},
    )
    assert outcome.exit_code is Exit.OK, outcome.detail
    events = client.events
    (output,) = [
        e
        for e in events
        if e["type"] == "session/update" and e["payload"]["update"].get("rawOutput") is not None
    ]
    update = output["payload"]["update"]
    assert update["rawOutput"] == {"exit_code": 127} and update["status"] == "failed", "the shim refused"
    refused = [e for e in events if e["type"] == EVENT]
    assert [e["payload"] for e in refused] == [{"tool": "kubectl", "tool_call_id": update["toolCallId"]}]
    assert refused[0]["seq"] == output["seq"] + 1, "journaled right after the call that printed it"


def test_the_shim_holds_no_credential_and_posts_nothing() -> None:
    """ADR 0115: nothing the agent runs uses the run token; the shim reaches no network either."""
    text = SHIM.read_text(encoding="utf-8")
    for forbidden in ("CHOREGOS_RUN_", "curl", "wget", "/dev/tcp", "CHOREGOS_API_URL"):
        assert forbidden not in text, f"the shim names {forbidden}"


def test_the_shimmed_tools_are_the_images() -> None:
    """The runner journals a refusal of a tool the image replaces with the shim, and only of one."""
    loop = re.search(r"for binary in ([^;]+); do", DOCKERFILE.read_text(encoding="utf-8"))
    assert loop is not None, "the Dockerfile's shim loop moved"
    assert set(loop.group(1).split()) == SHIMMED


def _update(kind: str, **fields: Any) -> dict[str, Any]:
    return {"sessionId": "s1", "update": {"sessionUpdate": kind, **fields}}


def _output(call: str, text: str) -> dict[str, Any]:
    """A call's output as a shell tool reports it: the call's text content."""
    content = [{"type": "content", "content": {"type": "text", "text": text}}]
    return _update("tool_call_update", toolCallId=call, content=content)


def test_a_refusal_is_one_event_per_call_and_tool_and_only_for_a_shimmed_tool() -> None:
    line = "choregos: `{}` is refused in an agent workspace."
    refusals = ShimRefusals()
    fenced = "```\n" + line.format("helm") + "\n```"
    assert refusals.of(_output("c1", fenced)) == [{"tool": "helm", "tool_call_id": "c1"}]
    assert refusals.of(_output("c1", line.format("helm"))) == [], "the same call, again"
    assert refusals.of(_output("c2", line.format("helm"))) == [{"tool": "helm", "tool_call_id": "c2"}]
    # A name the image does not shim says nothing: what an agent prints cannot name another tool.
    assert refusals.of(_output("c3", line.format("rm"))) == []
    # A refusal is read in a tool call's output only, not in what the agent says.
    said = {"type": "text", "text": line.format("aws")}
    assert refusals.of(_update("agent_message_chunk", content=said)) == []
    # In the raw output too, as some backends report it.
    raw = _update("tool_call_update", toolCallId="c4", rawOutput={"stderr": line.format("docker")})
    assert refusals.of(raw) == [{"tool": "docker", "tool_call_id": "c4"}]
