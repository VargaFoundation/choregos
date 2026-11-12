# SPDX-License-Identifier: Apache-2.0
"""A daemon killed in the middle of a write, for real: a process, `SIGKILL`, a restart.

The other suites restart a daemon object over the journal a former one left. This one runs the
command an operator runs, kills the process while the outpost's server holds the write, and starts
it again: nothing but what reached the disk before the kill can explain what the second process
does. It reports the call `unanswered`, and does not execute it again (ADR 0149 §5).

Everything the three commands print along the way is then searched for the instance's private keys
and for the endpoint's credential: none appears in an output, a log line or a file the daemon wrote.
"""

from __future__ import annotations

import asyncio
import json
import signal
import sys
from pathlib import Path
from typing import Any

import httpx
from choregos_adapters.fakes.mcp import FakeMcpServer
from choregos_outpost.fakes import FakeDoor

from ._bench import AUDIT_LINE, CREDENTIAL, TOOLS, policy_document, private_spellings, write_policy
from ._wire import served


async def _command(root: Path, *arguments: str) -> asyncio.subprocess.Process:
    base = ["--policy", str(root / "policy.yaml"), "--state-dir", str(root / "state"), "--log-level", "DEBUG"]
    return await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "choregos_outpost.cli",
        *base,
        *arguments,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={"PATH": "/usr/bin:/bin", "PYTHONUNBUFFERED": "1", "NO_PROXY": "*"},
    )


async def _finished(process: asyncio.subprocess.Process) -> tuple[int, str, str]:
    out, err = await asyncio.wait_for(process.communicate(), timeout=60)
    assert process.returncode is not None
    return process.returncode, out.decode(), err.decode()


async def test_a_daemon_killed_in_the_middle_of_a_write_reports_it_unanswered_and_does_not_execute_it_again(
    tmp_path: Path,
) -> None:
    door = FakeDoor(max_wait_s=0.2)
    server = FakeMcpServer(tools=TOOLS, token=CREDENTIAL)
    writes: list[dict[str, Any]] = []
    arrived = asyncio.Event()

    async def endpoint(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("method") == "tools/call":
            # The write reached the outpost's server, which never answers: the daemon dies meanwhile.
            writes.append(body["params"])
            arrived.set()
            await asyncio.sleep(3600)
        return server.respond(request)

    async with (
        served(door.handle, origin=lambda url: setattr(door, "origin", url)),
        served(endpoint) as tracker,
    ):
        write_policy(tmp_path, policy_document(door, tmp_path, url=tracker + "/mcp", writes="allow"))

        code, enrolled, enrol_err = await _finished(
            await _command(tmp_path, "enrol", "--token", door.enrolment_token())
        )
        assert code == 0, enrol_err
        (instance,) = door.instances.values()
        assert instance.fingerprint in enrolled
        door.approve(instance.id, instance.fingerprint)

        call = door.submit("tracker", "create_issue", {"title": "written once"}, lease_s=120)
        first = await _command(tmp_path, "run")
        await asyncio.wait_for(arrived.wait(), timeout=60)
        first.send_signal(signal.SIGKILL)
        code, first_out, first_err = await _finished(first)
        assert code == -signal.SIGKILL
        assert call.id not in door.answers and call.id not in door.refusals

        second = await _command(tmp_path, "run")
        answer = await door.answered(call.id, timeout_s=60)
        second.send_signal(signal.SIGTERM)
        code, second_out, second_err = await _finished(second)
        assert code == 0, second_err

        code, doctor_out, doctor_err = await _finished(await _command(tmp_path, "doctor"))
        assert code == 0, doctor_out + doctor_err

    assert (answer.outcome, answer.replayed, answer.write, answer.result) == ("unanswered", True, True, None)
    assert writes == [{"name": "create_issue", "arguments": {"title": "written once"}}], "executed once"
    log = [
        json.loads(line)
        for line in (tmp_path / "log" / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all(not list(AUDIT_LINE.iter_errors(line)) for line in log)
    assert [(line["event"], line.get("outcome")) for line in log if line["call_id"] == call.id] == [
        ("received", None),
        ("replayed", "unanswered"),
    ]
    said = [json.loads(line) for line in second_out.splitlines() if line.startswith("{")]
    assert any(line["event"] == "call.left_unanswered" and line["call_id"] == call.id for line in said)
    assert said[0]["event"] == "outpost.started" and said[-1]["event"] == "outpost.stopped"

    # No private key, and no credential, in anything the commands printed or the daemon wrote.
    printed = "\n".join(
        [enrolled, enrol_err, first_out, first_err, second_out, second_err, doctor_out, doctor_err]
    )
    assert "outpost.started" in first_out and instance.fingerprint in first_out, "the logs were captured"
    written = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in [*(tmp_path / "state").rglob("*"), tmp_path / "log" / "audit.jsonl"]
        if path.is_file() and path.name not in ("signing.key", "sealing.key")
    )
    for spelling in private_spellings(tmp_path / "state" / "identity"):
        assert spelling not in printed, "a private key in an output or a log line"
        assert spelling not in written, "a private key in a file that is not its own"
    assert CREDENTIAL not in printed and CREDENTIAL not in written
