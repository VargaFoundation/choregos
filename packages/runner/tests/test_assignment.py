"""A warm runner waits for ONE run, sealed to its own key (`await-assignment`, S35-02, ADR 0053)."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
import pytest
from choregos_adapters.sealing import key_from_log, new_key, seal
from choregos_runner import cli
from choregos_runner.assignment import AssignmentServer
from choregos_runner.exits import Exit
from choregos_runner.runner import RunOutcome

SECRET = {"api_url": "http://api/internal", "run_token": "tok-1", "env": {"HTTP_PROXY": "http://p"}}


def _sealed_for(server: AssignmentServer, run_id: str = "r-1", **secret: Any) -> dict[str, str]:
    return {"run_id": run_id, "sealed": seal(server.public_key, run_id, {**SECRET, **secret})}


async def _server() -> tuple[AssignmentServer, str]:
    server = AssignmentServer(host="127.0.0.1", port=0)
    await server.start()
    return server, f"http://127.0.0.1:{server.port}"


async def test_one_run_is_accepted_the_same_again_and_no_other() -> None:
    server, url = await _server()
    try:
        async with httpx.AsyncClient() as http:
            health = await http.get(f"{url}/healthz")
            assert health.status_code == 200 and health.json() == {"assigned": False}
            assert (await http.post(f"{url}/assign", json=_sealed_for(server))).status_code == 200
            again = await http.post(f"{url}/assign", json={"run_id": "r-1", "sealed": "anything"})
            assert again.status_code == 200, "a repeated push of the same run is answered"
            other = {"run_id": "r-2", "sealed": seal(server.public_key, "r-2", SECRET)}
            assert (await http.post(f"{url}/assign", json=other)).status_code == 409
            assert (await http.get(f"{url}/healthz")).json() == {"assigned": True}
        given = await asyncio.wait_for(server.wait(), timeout=1)
        assert (given.run_id, given.run_token, given.env) == ("r-1", "tok-1", {"HTTP_PROXY": "http://p"})
    finally:
        await server.close()


async def test_a_token_sealed_to_another_pods_key_is_refused() -> None:
    server, url = await _server()
    _, someone_else = new_key()
    try:
        async with httpx.AsyncClient() as http:
            stolen = {"run_id": "r-1", "sealed": seal(someone_else, "r-1", SECRET)}
            response = await http.post(f"{url}/assign", json=stolen)
            assert response.status_code == 400
            # Sealed to this pod, but for another run id: refused too (the run id is bound).
            moved = {"run_id": "r-2", "sealed": seal(server.public_key, "r-1", SECRET)}
            assert (await http.post(f"{url}/assign", json=moved)).status_code == 400
        assert server.assignment is None
    finally:
        await server.close()


async def test_once_opened_the_key_is_gone() -> None:
    server, url = await _server()
    try:
        async with httpx.AsyncClient() as http:
            await http.post(f"{url}/assign", json=_sealed_for(server))
        assert server._private is None, "the pod can open nothing else"
    finally:
        await server.close()


def test_the_key_line_is_what_the_executor_reads() -> None:
    server = AssignmentServer(host="127.0.0.1", port=0)
    assert key_from_log(f"{server.key_line}\nagent: hello\n") == server.public_key
    assert key_from_log("runner: no key here\n") is None


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[]",
        b'{"run_id": "r-1"}',
        b'{"run_id": "r-1", "api_url": "a", "run_token": "tok-in-clear", "env": {}}',
    ],
)
async def test_a_malformed_or_clear_assignment_is_refused_and_assigns_nothing(body: bytes) -> None:
    server, url = await _server()
    try:
        async with httpx.AsyncClient() as http:
            response = await http.post(f"{url}/assign", content=body)
            assert response.status_code == 400
            assert "tok" not in response.text
        assert server.assignment is None
    finally:
        await server.close()


async def test_an_oversized_assignment_is_refused() -> None:
    server, url = await _server()
    try:
        async with httpx.AsyncClient() as http:
            response = await http.post(f"{url}/assign", content=b"x" * (70 * 1024))
            assert response.status_code == 413
        assert server.assignment is None
    finally:
        await server.close()


async def test_the_stage_runs_only_after_the_assignment_with_its_token(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seen: list[dict[str, Any]] = []

    class _Runner:
        def __init__(self, settings: Any) -> None:
            self.settings = settings

        async def run(self) -> RunOutcome:
            seen.append(
                {
                    "run_id": self.settings.run_id,
                    "api_url": self.settings.api_url,
                    "token": self.settings.run_token,
                    "proxy": os.environ.get("HTTP_PROXY"),
                }
            )
            return RunOutcome(Exit.OK, detail="done")

    monkeypatch.setattr(cli, "Runner", _Runner)
    # The assignment updates the process environment: give the test a copy to dirty.
    monkeypatch.setattr(os, "environ", {k: v for k, v in os.environ.items() if k != "HTTP_PROXY"})
    started: list[AssignmentServer] = []
    real_start = AssignmentServer.start

    async def _start(self: AssignmentServer) -> None:
        self.host, self.port = "127.0.0.1", 0
        await real_start(self)
        started.append(self)

    monkeypatch.setattr(AssignmentServer, "start", _start)
    task = asyncio.create_task(cli._serve_then_run(8765, None))
    while not started:
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    assert seen == [], "nothing runs before an assignment"
    printed = key_from_log(capsys.readouterr().out)
    assert printed == started[0].public_key, "the public key is printed at boot, on its own line"
    async with httpx.AsyncClient() as http:
        body = {"run_id": "r-1", "sealed": seal(printed, "r-1", SECRET)}
        assert (await http.post(f"http://127.0.0.1:{started[0].port}/assign", json=body)).status_code == 200
    outcome, settings = await asyncio.wait_for(task, timeout=5)
    assert outcome.exit_code is Exit.OK and settings.run_id == "r-1"
    assert seen == [
        {"run_id": "r-1", "api_url": "http://api/internal", "token": "tok-1", "proxy": "http://p"}
    ]
