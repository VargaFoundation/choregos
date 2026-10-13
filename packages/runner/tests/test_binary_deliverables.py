"""Binary deliverables (S31-05, ADR 0049): what an agent writes in `.choregos/outputs/` is stored
as a typed artifact with its digest; a declared file that is absent fails the step."""

from __future__ import annotations

import base64
import hashlib
import io
import subprocess
import zipfile
from typing import Any

from choregos_contracts import StageInput, StageStatus
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, valid_result

PPTX = "application/vnd.openxmlformats-officedocument.presentationml.presentation"


def _deck() -> bytes:
    """A minimal OOXML package: what a skill run in the sandbox would leave."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/presentation.xml", "<p:presentation/>")
    return buffer.getvalue()


DECK = _deck()
CLAIMED = {
    "filename": "x.pptx",
    "type": "pptx",
    "media_type": PPTX,
    "digest": "sha256:" + "0" * 64,
    "size": 1,
}


def _write(path: str, data: bytes) -> dict[str, str]:
    return {"path": path, "b64": base64.b64encode(data).decode()}


async def _execute(
    stage_input: StageInput, settings: Any, turns: list[dict[str, Any]], *, refuse: set[str] | None = None
) -> tuple[Any, FakeInternalClient]:
    agent_script({"turns": turns}, stage_input, settings.workspace)
    client = FakeInternalClient(stage_input)
    client.refuse_outputs = refuse or set()  # type: ignore[attr-defined]
    outcome = await Runner(settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client


def _declare(stage_input: StageInput, **types: str) -> None:
    stage_input.transition.outputs = list(types)
    stage_input.transition.output_types = dict(types)


async def test_a_fake_agents_pptx_is_stored_with_its_digest_and_media_type(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _declare(stage_input, deck="pptx")
    chart = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    outcome, client = await _execute(
        stage_input,
        runner_settings,
        [
            {
                "writes": [
                    _write(".choregos/outputs/deck.pptx", DECK),
                    _write(".choregos/outputs/chart.png", chart),
                ],
                # The agent cannot vouch for its own files: what it writes here is replaced.
                "result": valid_result("deck ready", artifacts={"files": {"fake": CLAIMED}}),
            }
        ],
    )
    assert outcome.exit_code is Exit.OK, outcome.detail
    result = client.results[0]
    assert result.status is StageStatus.DONE, result.summary
    digest = "sha256:" + hashlib.sha256(DECK).hexdigest()
    assert client.outputs["deck"] == ("pptx", DECK, digest)  # type: ignore[attr-defined]
    deck = result.artifacts.files["deck"]
    assert (deck.filename, deck.type, deck.media_type, deck.digest, deck.size) == (
        "deck.pptx",
        "pptx",
        PPTX,
        digest,
        len(DECK),
    )
    # An undeclared file is kept too, under its own name and the type of its extension.
    assert result.artifacts.files["chart.png"].media_type == "image/png"
    assert set(result.artifacts.files) == {"deck", "chart.png"}
    tracked = subprocess.run(
        ["git", "ls-files", ".choregos"],
        cwd=runner_settings.workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "outputs" not in tracked.stdout, "a deliverable is an artifact, never a commit"
    task = (runner_settings.workspace / ".choregos" / "task.md").read_text(encoding="utf-8")
    assert "`.choregos/outputs/deck.pptx` (pptx)" in task


async def test_a_declared_xlsx_that_is_absent_fails_the_step(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _declare(stage_input, deck="pptx", budget="xlsx")
    outcome, client = await _execute(
        stage_input,
        runner_settings,
        [{"writes": [_write(".choregos/outputs/deck.pptx", DECK)], "result": valid_result("deck ready")}],
    )
    assert outcome.exit_code is Exit.INVALID_RESULT
    result = client.results[0]
    assert result.status is StageStatus.FAILED
    assert "budget.xlsx" in result.summary
    assert "budget" not in result.artifacts.files
    repairs = [e for e in client.events if e["type"] == "result.repair"]
    assert repairs and "budget.xlsx" in repairs[0]["payload"]["error"], "the agent was told what is missing"


async def test_a_declared_file_the_platform_could_not_store_fails_the_step(
    stage_input: StageInput, runner_settings: Any
) -> None:
    _declare(stage_input, deck="pptx")
    outcome, client = await _execute(
        stage_input,
        runner_settings,
        [{"writes": [_write(".choregos/outputs/deck.pptx", DECK)], "result": valid_result("deck ready")}],
        refuse={"deck"},
    )
    assert outcome.exit_code is Exit.OK
    result = client.results[0]
    assert result.status is StageStatus.FAILED and result.reason == "missing_outputs"
    assert result.artifacts.files == {}


async def test_a_symbolic_link_in_the_outputs_is_not_stored(
    stage_input: StageInput, runner_settings: Any
) -> None:
    outputs = runner_settings.workspace / ".choregos" / "outputs"
    outputs.mkdir(parents=True)
    (outputs / "secrets.txt").symlink_to("/etc/hostname")
    outcome, client = await _execute(stage_input, runner_settings, [{"result": valid_result()}])
    assert outcome.exit_code is Exit.OK, outcome.detail
    assert client.results[0].artifacts.files == {}
    assert not getattr(client, "outputs", {})
