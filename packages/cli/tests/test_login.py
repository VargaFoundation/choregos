"""`choregos login` signs in as the person by the device flow (ADR 0154 §6, S67-05), against a fake API."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

API = "http://api.test"
SECRET = "chs_the-session-credential"


@pytest.fixture
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = tmp_path / "config.json"
    monkeypatch.setenv("CHOREGOS_CONFIG", str(path))
    import tranibor_cli.client as client_module
    import tranibor_cli.device_login as device_login

    monkeypatch.setattr(client_module, "CONFIG_PATH", path)
    monkeypatch.setattr(device_login, "sleep", lambda _seconds: None)
    monkeypatch.setattr("webbrowser.open", lambda _url: True)
    return path


def _run(*args: str) -> Any:
    from tranibor_cli.main import app

    return CliRunner().invoke(app, list(args))


def _codes(contract: Any) -> dict[str, Any]:
    return contract(
        "DeviceAuthorization",
        {
            "device_code": "dev-code-123",
            "user_code": "BCDF-GH23",
            "verification_uri": "http://console.test/device",
            "verification_uri_complete": "http://console.test/device?user_code=BCDF-GH23",
            "expires_in": 600,
            "interval": 5,
        },
    )


def _error(name: str, **extra: Any) -> Response:
    return Response(400, json={"error": name, "error_description": name, **extra})


def _session(contract: Any) -> Response:
    return Response(
        200,
        json=contract(
            "DeviceToken",
            {
                "access_token": SECRET,
                "token_type": "Bearer",
                "session_id": "ses-1",
                "kind": "cli",
                "expires_in": 604800,
                "auth_time": "2026-11-16T10:00:00Z",
            },
        ),
    )


@respx.mock
def test_login_shows_the_code_waits_and_keeps_the_session_without_printing_it(
    profile: Path, contract: Any
) -> None:
    start = respx.post(f"{API}/api/v1/auth/device").mock(return_value=Response(200, json=_codes(contract)))
    respx.post(f"{API}/api/v1/auth/device/token").mock(
        side_effect=[_error("authorization_pending"), _error("slow_down", interval=10), _session(contract)]
    )
    respx.get(f"{API}/api/v1/me").mock(
        return_value=Response(200, json={"email": "v@example.test", "memberships": []})
    )
    result = _run("login", "--api-url", API, "--org", "acme", "--no-browser")
    assert result.exit_code == 0, result.output
    assert "BCDF-GH23" in result.output and "http://console.test/device?user_code=BCDF-GH23" in result.output
    assert "signed in as v@example.test" in result.output
    assert SECRET not in result.output, "the credential is never printed"
    sent = json.loads(start.calls[0].request.content)
    assert (
        sent["client_kind"] == "cli" and sent["org"] == "acme" and sent["client_name"].startswith("tranibor ")
    )
    assert json.loads(profile.read_text())["token"] == SECRET
    assert oct(profile.stat().st_mode & 0o777) == "0o600"
    assert respx.calls.last.request.headers["authorization"] == f"Bearer {SECRET}"


@respx.mock
def test_login_fresh_asks_the_console_for_a_recent_sign_in(profile: Path, contract: Any) -> None:
    respx.post(f"{API}/api/v1/auth/device").mock(return_value=Response(200, json=_codes(contract)))
    respx.post(f"{API}/api/v1/auth/device/token").mock(return_value=_session(contract))
    respx.get(f"{API}/api/v1/me").mock(return_value=Response(200, json={"email": "v@example.test"}))
    respx.get(f"{API}/api/v1/orgs").mock(return_value=Response(200, json=[]))
    result = _run("login", "--api-url", API, "--fresh", "--no-browser")
    assert result.exit_code == 0, result.output
    assert "user_code=BCDF-GH23&fresh=1" in result.output.replace("\n", "")


@respx.mock
def test_login_slow_down_lengthens_the_wait(
    profile: Path, contract: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tranibor_cli.device_login as device_login

    waits: list[float] = []
    monkeypatch.setattr(device_login, "sleep", waits.append)
    respx.post(f"{API}/api/v1/auth/device").mock(return_value=Response(200, json=_codes(contract)))
    respx.post(f"{API}/api/v1/auth/device/token").mock(
        side_effect=[_error("slow_down", interval=10), _error("authorization_pending"), _session(contract)]
    )
    respx.get(f"{API}/api/v1/me").mock(return_value=Response(200, json={"email": "v@example.test"}))
    respx.get(f"{API}/api/v1/orgs").mock(return_value=Response(200, json=[]))
    assert _run("login", "--api-url", API, "--no-browser").exit_code == 0
    assert waits == [5.0, 10.0, 10.0]


@respx.mock
@pytest.mark.parametrize(
    ("error", "said"),
    [("access_denied", "refused in the console"), ("expired_token", "expired before it was approved")],
)
def test_login_says_why_it_did_not_sign_in_and_saves_nothing(
    profile: Path, contract: Any, error: str, said: str
) -> None:
    respx.post(f"{API}/api/v1/auth/device").mock(return_value=Response(200, json=_codes(contract)))
    respx.post(f"{API}/api/v1/auth/device/token").mock(return_value=_error(error))
    result = _run("login", "--api-url", API, "--no-browser")
    assert result.exit_code == 1
    assert said in " ".join(result.output.split())
    assert not profile.exists()


@respx.mock
def test_login_gives_up_when_the_code_expires_unanswered(
    profile: Path, contract: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tranibor_cli.device_login as device_login

    ticks = iter(range(0, 10_000, 400))
    monkeypatch.setattr(device_login, "clock", lambda: float(next(ticks)))
    respx.post(f"{API}/api/v1/auth/device").mock(return_value=Response(200, json=_codes(contract)))
    respx.post(f"{API}/api/v1/auth/device/token").mock(return_value=_error("authorization_pending"))
    result = _run("login", "--api-url", API, "--no-browser")
    assert result.exit_code == 1
    assert "expired" in result.output


@respx.mock
def test_login_with_a_token_keeps_the_old_path(profile: Path) -> None:
    respx.get(f"{API}/api/v1/me").mock(return_value=Response(200, json={"email": "v@example.test"}))
    respx.get(f"{API}/api/v1/orgs").mock(return_value=Response(200, json=[]))
    result = _run("login", "--api-url", API, "--token", "chg_x")
    assert result.exit_code == 0, result.output
    assert json.loads(profile.read_text())["token"] == "chg_x"
    assert not [c for c in respx.calls if c.request.url.path.startswith("/api/v1/auth/device")]


@respx.mock
def test_logout_ends_the_session_on_the_server_and_forgets_it_here(profile: Path) -> None:
    profile.write_text(json.dumps({"api_url": API, "token": SECRET, "org": "acme"}))
    respx.get(f"{API}/api/v1/me/sessions").mock(
        return_value=Response(
            200, json=[{"id": "ses-other", "current": False}, {"id": "ses-1", "current": True}]
        )
    )
    ended = respx.delete(f"{API}/api/v1/me/sessions/ses-1").mock(return_value=Response(204))
    result = _run("logout")
    assert result.exit_code == 0, result.output
    assert ended.called and len(respx.calls) == 2, "only this CLI's own session is ended"
    assert json.loads(profile.read_text())["token"] == ""
    assert SECRET not in result.output


@respx.mock
def test_logout_forgets_the_credential_even_when_the_session_was_already_ended(profile: Path) -> None:
    profile.write_text(json.dumps({"api_url": API, "token": SECRET, "org": "acme"}))
    respx.get(f"{API}/api/v1/me/sessions").mock(return_value=Response(401, json={"detail": "session ended"}))
    result = _run("logout")
    assert result.exit_code == 0, result.output
    assert json.loads(profile.read_text())["token"] == ""


def _signs_in(contract: Any) -> None:
    respx.post(f"{API}/api/v1/auth/device").mock(return_value=Response(200, json=_codes(contract)))
    respx.post(f"{API}/api/v1/auth/device/token").mock(return_value=_session(contract))
    respx.get(f"{API}/api/v1/me").mock(return_value=Response(200, json={"email": "v@example.test"}))
    respx.get(f"{API}/api/v1/orgs").mock(return_value=Response(200, json=[_org("acme")]))


def _org(slug: str) -> dict[str, Any]:
    return {"slug": slug, "name": slug.title(), "role": "member"}


@pytest.fixture
def desktop(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """A person at their own screen: no SSH, a display; the URLs the browser was asked to open."""
    opened: list[str] = []
    for name in ("SSH_CONNECTION", "SSH_TTY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr("choregos_cli.main._wsl", lambda: False)
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url) or True)
    return opened


@respx.mock
def test_login_opens_the_browser_by_default_and_still_gives_the_link(
    profile: Path, contract: Any, desktop: list[str]
) -> None:
    _signs_in(contract)
    result = _run("login", "--api-url", API)
    assert result.exit_code == 0, result.output
    assert desktop == ["http://console.test/device?user_code=BCDF-GH23"]
    assert "Opening your browser to sign in to api.test" in result.output
    assert "If it does not open, visit: http://console.test/device?user_code=BCDF-GH23" in result.output
    assert "BCDF-GH23" in result.output


@respx.mock
def test_login_over_ssh_prints_the_link_and_opens_nothing(
    profile: Path, contract: Any, desktop: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SSH_CONNECTION", "192.0.2.1 50000 192.0.2.2 22")
    _signs_in(contract)
    result = _run("login", "--api-url", API)
    assert result.exit_code == 0, result.output
    assert desktop == [], "no browser is started on a remote machine"
    assert "Open this link in a browser to sign in to api.test" in result.output
    assert "http://console.test/device?user_code=BCDF-GH23" in result.output


@respx.mock
def test_login_says_open_the_link_when_the_browser_cannot_start(
    profile: Path, contract: Any, desktop: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("webbrowser.open", lambda _url: False)
    _signs_in(contract)
    result = _run("login", "--api-url", API)
    assert result.exit_code == 0, result.output
    assert "Opening your browser" not in result.output
    assert "Open this link in a browser" in result.output


@respx.mock
def test_login_without_an_address_signs_in_to_the_saved_instance(
    profile: Path, contract: Any, desktop: list[str]
) -> None:
    profile.write_text(json.dumps({"api_url": API, "token": "", "org": "acme"}))
    _signs_in(contract)
    respx.get(f"{API}/api/v1/orgs").mock(return_value=Response(200, json=[_org("globex"), _org("acme")]))
    result = _run("login")
    assert result.exit_code == 0, result.output
    assert "Instance address" not in result.output, "a known instance is not asked again"
    saved = json.loads(profile.read_text())
    assert saved["api_url"] == API
    assert saved["org"] == "acme", "signing in again keeps the organisation chosen the first time"


@respx.mock
def test_login_saves_the_only_organisation_of_the_person(
    profile: Path, contract: Any, desktop: list[str]
) -> None:
    _signs_in(contract)
    result = _run("login", "--api-url", API)
    assert result.exit_code == 0, result.output
    assert json.loads(profile.read_text())["org"] == "acme"
    assert "signed in as v@example.test in acme" in " ".join(result.output.split())


@respx.mock
def test_login_names_the_organisations_it_cannot_choose_between(
    profile: Path, contract: Any, desktop: list[str]
) -> None:
    _signs_in(contract)
    respx.get(f"{API}/api/v1/orgs").mock(return_value=Response(200, json=[_org("acme"), _org("globex")]))
    result = _run("login", "--api-url", API)
    assert result.exit_code == 0, result.output
    assert json.loads(profile.read_text())["org"] == "", "no organisation is picked for the person"
    said = " ".join(result.output.split())
    assert "acme, globex" in said and "choregos login --org <slug>" in said


@respx.mock
def test_login_asks_which_organisation_in_a_terminal(
    profile: Path, contract: Any, desktop: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("choregos_cli.chat.interactive", lambda: True)
    _signs_in(contract)
    respx.get(f"{API}/api/v1/orgs").mock(return_value=Response(200, json=[_org("acme"), _org("globex")]))
    from choregos_cli.main import app

    result = CliRunner().invoke(app, ["login", "--api-url", API], input="initech\nglobex\n")
    assert result.exit_code == 0, result.output
    assert "'initech' is not one of yours" in result.output, "a slug outside the list is asked again"
    assert json.loads(profile.read_text())["org"] == "globex"


@respx.mock
def test_login_under_wsl_starts_the_windows_browser_and_hides_its_output(
    profile: Path, contract: Any, desktop: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    started: list[tuple[list[str], dict[str, Any]]] = []
    monkeypatch.setattr("choregos_cli.main._wsl", lambda: True)
    monkeypatch.setattr("choregos_cli.main.shutil.which", lambda _name: None)
    monkeypatch.setattr("choregos_cli.main.subprocess.Popen", lambda cmd, **kw: started.append((cmd, kw)))
    _signs_in(contract)
    result = _run("login", "--api-url", API)
    assert result.exit_code == 0, result.output
    assert desktop == [], "webbrowser is not used under WSL"
    ((command, options),) = started
    assert command == [
        "rundll32.exe",
        "url.dll,FileProtocolHandler",
        "http://console.test/device?user_code=BCDF-GH23",
    ]
    assert options["stdout"] is options["stderr"] is subprocess.DEVNULL, (
        "nothing of the browser reaches the screen"
    )
    assert "Opening your browser" in result.output


def test_a_bare_choregos_takes_the_organisation_of_its_workspace(monkeypatch: pytest.MonkeyPatch) -> None:
    opened: list[str | None] = []
    monkeypatch.setattr("choregos_cli.chat.interactive", lambda: True)
    monkeypatch.setattr("choregos_cli.chat.open_workspace", lambda *, org=None: opened.append(org))
    result = _run("--org", "diametral")
    assert result.exit_code == 0, result.output
    assert opened == ["diametral"]
