"""The run's credentials at the egress proxy (ADR 0070)."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote, urlsplit

import pytest
from choregos_runner.egress import authenticate_proxy_env

PROXY = "http://choregos-egress.choregos.svc:3128"
RUN = "billing-123-t-implement-1"


def _token() -> str:
    return "a.b-c_d/e+f=" + os.urandom(4).hex()  # built at run time; carries characters a URL must quote


def _env(**extra: str) -> dict[str, str]:
    return {
        "CHOREGOS_EGRESS_AUTH": "run",
        "HTTP_PROXY": PROXY,
        "HTTPS_PROXY": PROXY,
        "http_proxy": PROXY,
        "https_proxy": PROXY,
        "NO_PROXY": "localhost,.svc",
        **extra,
    }


def test_credentials_are_added_to_the_four_variables_and_the_token_is_quoted() -> None:
    token = _token()
    changes = authenticate_proxy_env(_env(), RUN, token)
    assert set(changes) == {"HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"}
    for value in changes.values():
        parts = urlsplit(value)
        assert (parts.username, parts.hostname, parts.port) == (RUN, "choregos-egress.choregos.svc", 3128)
        assert parts.password == quote(token, safe="")
    assert "NO_PROXY" not in changes


@pytest.mark.parametrize("auth", [None, "", "other"])
def test_nothing_changes_unless_the_deployment_asks_for_run_credentials(auth: str | None) -> None:
    env = _env()
    if auth is None:
        del env["CHOREGOS_EGRESS_AUTH"]
    else:
        env["CHOREGOS_EGRESS_AUTH"] = auth
    assert authenticate_proxy_env(env, RUN, _token()) == {}


def test_nothing_changes_without_a_token_or_a_run_id() -> None:
    assert authenticate_proxy_env(_env(), RUN, "") == {}
    assert authenticate_proxy_env(_env(), "", _token()) == {}


def test_a_proxy_that_already_carries_credentials_is_left_alone() -> None:
    mine = "http://operator:secret@proxy.example:3128"
    changes = authenticate_proxy_env(_env(HTTPS_PROXY=mine, https_proxy=mine), RUN, _token())
    assert set(changes) == {"HTTP_PROXY", "http_proxy"}


def test_unset_variables_are_not_invented() -> None:
    changes = authenticate_proxy_env({"CHOREGOS_EGRESS_AUTH": "run", "HTTPS_PROXY": PROXY}, RUN, _token())
    assert set(changes) == {"HTTPS_PROXY"}


def test_the_runner_applies_them_before_anything_else_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """`Runner.run` updates the process environment first, so git, pip and the agent inherit it."""
    import asyncio

    from choregos_runner.config import RunnerSettings
    from choregos_runner.runner import Runner

    token = _token()
    for name, value in _env().items():
        monkeypatch.setenv(name, value)
    settings = RunnerSettings(run_id=RUN, run_token=token, api_url="http://api.invalid")

    egress = "egress." + _token()

    class Dead:
        async def fetch_egress_token(self) -> str:
            return egress

        async def fetch_input(self) -> object:
            raise RuntimeError("stop here")

        async def aclose(self) -> None:
            return None

    asyncio.run(Runner(settings, Dead()).run())  # type: ignore[arg-type]
    assert urlsplit(os.environ["HTTPS_PROXY"]).username == RUN
    # ADR 0115: the egress token, never the run token, since the agent inherits these variables.
    assert urlsplit(os.environ["https_proxy"]).password == quote(egress, safe="")
    assert quote(token, safe="") not in os.environ["https_proxy"]
    assert os.environ["NO_PROXY"] == "localhost,.svc"


# ───────────────────────── what the agent inherits (ADR 0115) ─────────────────────────


async def _run_with_proxy(
    monkeypatch: pytest.MonkeyPatch, stage_input: Any, runner_settings: Any, egress_token: Any
) -> tuple[str, Any]:
    from choregos_runner.runner import Runner

    from .conftest import FakeInternalClient, agent_script, valid_result

    for name, value in _env().items():
        monkeypatch.setenv(name, value)
    runner_settings.run_token = "the-run-token-" + os.urandom(4).hex()
    agent_script({"turns": [{"result": valid_result()}]}, stage_input, runner_settings.workspace)
    client = FakeInternalClient(stage_input)
    client.egress_token = egress_token
    await Runner(runner_settings, client).run()  # type: ignore[arg-type]
    return os.environ["HTTPS_PROXY"], client


async def test_the_agent_inherits_the_egress_token_never_the_run_token(
    monkeypatch: pytest.MonkeyPatch, stage_input: Any, runner_settings: Any
) -> None:
    proxy, client = await _run_with_proxy(
        monkeypatch, stage_input, runner_settings, "egress-" + os.urandom(4).hex()
    )
    parts = urlsplit(proxy)
    assert parts.password == client.egress_token
    assert runner_settings.run_token not in proxy
    assert client.egress_token_calls == 1


async def test_an_api_without_egress_tokens_keeps_the_run_token(
    monkeypatch: pytest.MonkeyPatch, stage_input: Any, runner_settings: Any
) -> None:
    from choregos_runner.client import InternalApiError

    proxy, _ = await _run_with_proxy(
        monkeypatch, stage_input, runner_settings, InternalApiError(404, "Not Found")
    )
    assert urlsplit(proxy).password == quote(runner_settings.run_token, safe="")


async def test_without_proxy_authentication_no_egress_token_is_asked(
    monkeypatch: pytest.MonkeyPatch, stage_input: Any, runner_settings: Any
) -> None:
    monkeypatch.setenv("CHOREGOS_EGRESS_AUTH", "")
    from choregos_runner.runner import Runner

    from .conftest import FakeInternalClient, agent_script, valid_result

    agent_script({"turns": [{"result": valid_result()}]}, stage_input, runner_settings.workspace)
    client = FakeInternalClient(stage_input)
    await Runner(runner_settings, client).run()  # type: ignore[arg-type]
    assert client.egress_token_calls == 0
