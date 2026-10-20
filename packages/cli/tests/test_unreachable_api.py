"""An unreachable API is one clean line and exit code 1, never a traceback (S37-10c, #754)."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
from typer.testing import CliRunner


def _closed_port_url() -> str:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}"


@pytest.fixture
def api_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    url = _closed_port_url()
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"api_url": url, "token": "chg_x", "org": "acme"}))
    monkeypatch.setenv("CHOREGOS_CONFIG", str(path))
    import choregos_cli.client as client_module

    monkeypatch.setattr(client_module, "CONFIG_PATH", path)
    return url


def _run(*args: str) -> Any:
    from choregos_cli.main import app

    return CliRunner().invoke(app, list(args))


def _assert_clean(result: Any, url: str) -> None:
    assert result.exit_code == 1, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    flat = " ".join(result.output.split())  # rich wraps long lines
    assert "Traceback" not in flat
    assert f"cannot reach {url}" in flat
    assert "choregos login --api-url" in flat


def test_whoami_on_a_closed_port_fails_cleanly(api_url: str) -> None:
    _assert_clean(_run("whoami"), api_url)


def test_tokens_list_on_a_closed_port_fails_cleanly(api_url: str) -> None:
    _assert_clean(_run("tokens", "list"), api_url)


def test_login_on_a_closed_port_fails_cleanly(api_url: str) -> None:
    _assert_clean(_run("login", "--api-url", api_url, "--token", "chg_x"), api_url)


def test_a_timeout_is_reported_with_its_type(api_url: str) -> None:
    with respx.mock(base_url=api_url) as mock:
        mock.get("/api/v1/me").mock(side_effect=httpx.ReadTimeout(""))
        result = _run("whoami")
    _assert_clean(result, api_url)
    assert "ReadTimeout" in result.output


def test_a_failing_stream_fails_cleanly(api_url: str) -> None:
    with respx.mock(base_url=api_url) as mock:
        mock.get("/api/v1/runs/r1/events").mock(side_effect=httpx.ConnectError("refused"))
        result = _run("runs", "tail", "r1")
    _assert_clean(result, api_url)


def test_login_with_a_refused_token_still_says_sign_in_refused(api_url: str) -> None:
    """The transport guard keeps `login`'s own message for an API error."""
    with respx.mock(base_url=api_url) as mock:
        mock.get("/api/v1/me").mock(return_value=httpx.Response(401, json={"detail": "bad token"}))
        result = _run("login", "--api-url", api_url, "--token", "chg_bad")
    assert result.exit_code == 1, result.output
    assert "sign-in refused" in " ".join(result.output.split())


def test_login_help_says_the_first_token_comes_from_the_console() -> None:
    result = _run("login", "--help")
    flat = " ".join(result.output.split())
    assert "cannot issue the first token" in flat
    assert "/admin" in flat


def test_a_knowledge_command_fails_cleanly(api_url: str, tmp_path: Path) -> None:
    manifest = tmp_path / "kb.yaml"
    manifest.write_text("apiVersion: choregos/v1\nkind: KnowledgeBase\nslug: acme\nname: Acme\n")
    result = _run("knowledge", "apply", "-f", str(manifest))
    _assert_clean(result, api_url)
