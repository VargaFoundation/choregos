"""A downstream run sees its declared inputs as files in `.choregos/inputs/` (S31-03, ADR 0049)."""

from __future__ import annotations

import hashlib
import subprocess
from typing import Any

from choregos_contracts import StageInput
from choregos_runner.exits import Exit
from choregos_runner.runner import Runner

from .conftest import FakeInternalClient, agent_script, valid_result

DECK = b"PK\x03\x04" + bytes(range(256)) * 4
PROFILES = b'[{"name":"Ada","score":9}]'


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _entry(name: str, filename: str, data: bytes, media_type: str) -> dict[str, Any]:
    return {
        "name": name,
        "version": 1,
        "filename": filename,
        "type": filename.rsplit(".", 1)[-1],
        "media_type": media_type,
        "digest": _digest(data),
        "size": len(data),
    }


async def _run(
    stage_input: StageInput, settings: Any, served: dict[str, tuple[dict[str, Any], bytes]]
) -> tuple[Any, FakeInternalClient]:
    stage_input.transition.inputs = ["deck", "profiles"]
    agent_script(
        {
            "turns": [
                {
                    "reads": [".choregos/inputs/deck.pptx", ".choregos/inputs/profiles.json"],
                    "result": valid_result("read the deck"),
                }
            ]
        },
        stage_input,
        settings.workspace,
    )
    client = FakeInternalClient(stage_input)
    client.inputs = served
    outcome = await Runner(settings, client).execute(stage_input, client)  # type: ignore[arg-type]
    return outcome, client


async def test_the_agent_finds_each_input_as_a_file(stage_input: StageInput, runner_settings: Any) -> None:
    served = {
        "deck": (
            _entry(
                "deck",
                "deck.pptx",
                DECK,
                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
            ),
            DECK,
        ),
        "profiles": (_entry("profiles", "profiles.json", PROFILES, "application/json"), PROFILES),
    }
    outcome, client = await _run(stage_input, runner_settings, served)
    assert outcome.exit_code is Exit.OK, outcome.detail
    seen = client.results[0].outputs.model_dump()["seen"]
    assert seen == {
        ".choregos/inputs/deck.pptx": _digest(DECK),
        ".choregos/inputs/profiles.json": _digest(PROFILES),
    }, "the agent process read the exact bytes the platform recorded"
    task = (runner_settings.workspace / ".choregos" / "task.md").read_text(encoding="utf-8")
    assert "`.choregos/inputs/deck.pptx`" in task
    # Mounted, never committed.
    tracked = subprocess.run(
        ["git", "ls-files", ".choregos"],
        cwd=runner_settings.workspace,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "inputs" not in tracked.stdout


async def test_an_input_that_does_not_match_its_digest_stops_the_run(
    stage_input: StageInput, runner_settings: Any
) -> None:
    entry = _entry("deck", "deck.pptx", DECK, "application/octet-stream")
    outcome, client = await _run(stage_input, runner_settings, {"deck": (entry, DECK + b"tampered")})
    assert outcome.exit_code is Exit.INPUTS_INVALID
    assert "deck" in outcome.detail
    assert not client.results, "no agent ran on content that is not what was produced"
    assert not (runner_settings.workspace / ".choregos" / "inputs" / "deck.pptx").exists()


async def test_an_unsafe_file_name_is_refused(stage_input: StageInput, runner_settings: Any) -> None:
    entry = {**_entry("deck", "deck.pptx", DECK, "application/octet-stream"), "filename": "../escape"}
    outcome, _ = await _run(stage_input, runner_settings, {"deck": (entry, DECK)})
    assert outcome.exit_code is Exit.INPUTS_INVALID
