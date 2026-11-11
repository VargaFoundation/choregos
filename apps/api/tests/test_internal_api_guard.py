"""The run's API answers only where runs are (S65-02, ADR 0144).

`/api/v1/internal/*` is what a run's pod calls with its run token. The deployment routes the whole
`/api` prefix of its public hostnames to the API, so those routes answered there too. With
`CHOREGOS_INTERNAL_API_GUARD=enforce` a request that arrives under a public hostname gets what an
unknown route gets, unless the hostname is a run door; the Service's name is served as before.

The deployment these tests describe: a console on `app.example.com`, an API on `api.example.com`,
runs sent to the in-cluster Service.
"""

from __future__ import annotations

import datetime
import json
from typing import Any

import pytest
import structlog
from choregos_api.config import Settings, reset_settings_cache
from choregos_api.internal_api_guard import (
    PREFIX,
    Guard,
    InternalApiGuard,
    announce,
    guard_of,
    hostname,
    presented_hosts,
)
from httpx import AsyncClient
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from .test_network_policy_api import _run

SERVICE = "choregos-api:8000"
API = "api.example.com"
CONSOLE = "app.example.com"
DOOR = "runs.example.com"
UNKNOWN = "/api/v1/no-such-route"


def _deployed(monkeypatch: pytest.MonkeyPatch, mode: str | None, **more: str) -> None:
    """The settings of a deployment whose runs call the Service, with the guard in `mode`."""
    settings = {
        "CHOREGOS_PUBLIC_URL": f"https://{CONSOLE}",
        "CHOREGOS_API_URL": f"https://{API}",
        "CHOREGOS_CORS_ORIGINS": f"https://{CONSOLE}",
        "CHOREGOS_INTERNAL_API_URL": f"http://{SERVICE}",
        **more,
    }
    if mode is not None:
        settings["CHOREGOS_INTERNAL_API_GUARD"] = mode
    for name, value in settings.items():
        monkeypatch.setenv(name, value)
    reset_settings_cache()


def _network(run_id: str) -> str:
    return f"{PREFIX}/runs/{run_id}/network"


async def _on(client: AsyncClient, host: str, path: str, token: dict[str, str], **headers: str) -> Any:
    return await client.get(path, headers={**token, "Host": host, **headers})


async def _session_pod(project: dict[str, Any]) -> tuple[str, dict[str, str]]:
    """A hosted session whose pod may read its input, and the token its workflow mints for it."""
    from choregos_api.db.models import AgentSessionRecord, Organization
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token
    from sqlalchemy import select

    async with session_scope() as db:
        org_id = (await db.execute(select(Organization.id).where(Organization.slug == "varga"))).scalar_one()
        record = AgentSessionRecord(
            org_id=org_id,
            project_id=project["id"],
            agent_slug="triager",
            agent_version=1,
            user_id="admin@varga.dev",
            expires_at=datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=1),
            stage_input={"run_id": "session-x", "callbacks": {"run_token": "***"}},
        )
        db.add(record)
        await db.flush()
        session_id = str(record.id)
    token = mint_run_token(
        f"session-{session_id}",
        project_slug="billing-api",
        work_item_key=f"session:{session_id}",
        ttl_minutes=30,
    )
    return f"{PREFIX}/agent-sessions/{session_id}/input", {"Authorization": f"Bearer {token}"}


# ───────────────────────── enforce ─────────────────────────


@pytest.mark.parametrize("host", [API, CONSOLE, f"{API}:443", "API.Example.COM", f"{API}."])
async def test_a_public_host_gets_what_an_unknown_route_gets(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    token = await _run(project, "run-guard-1")
    _deployed(monkeypatch, "enforce")

    served = await _on(client, SERVICE, _network("run-guard-1"), token)
    assert served.status_code == 200, served.text

    refused = await _on(client, host, _network("run-guard-1"), token)
    unknown = await _on(client, host, UNKNOWN, token)
    assert unknown.status_code == 404
    assert (refused.status_code, refused.content, refused.headers["content-type"]) == (
        unknown.status_code,
        unknown.content,
        unknown.headers["content-type"],
    ), "a refusal must not be told apart from a route that does not exist"
    assert refused.headers["x-request-id"], "the refusal is answered inside the logging middleware"


async def test_an_encoded_path_is_the_same_path(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The router matches the decoded path, so the guard reads the decoded path."""
    token = await _run(project, "run-guard-8")
    _deployed(monkeypatch, "enforce")
    encoded = "/api/v1/%69nternal/runs/run-guard-8/network"
    assert (await _on(client, SERVICE, encoded, token)).status_code == 200, "the router does route it"
    assert (await _on(client, API, encoded, token)).status_code == 404


async def test_a_session_pods_routes_are_guarded_too(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    url, token = await _session_pod(project)
    _deployed(monkeypatch, "enforce")

    served = await _on(client, SERVICE, url, token)
    assert served.status_code == 200, served.text
    assert served.json()["run_id"] == "session-x"

    refused = await _on(client, API, url, token)
    assert refused.status_code == 404
    assert refused.json() == {"detail": "Not Found"}


async def test_every_route_under_the_prefix_is_guarded_whatever_declares_it(
    client: AsyncClient, org: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The relay's route lives in another router: the guard reads the path, not the router."""
    _deployed(monkeypatch, "enforce")
    url = f"{PREFIX}/orgs/varga/outbound-proxy/web_fetch"
    assert (await _on(client, SERVICE, url, {})).status_code == 401, "the route asks for its token"
    assert (await _on(client, API, url, {})).status_code == 404
    assert (await _on(client, API, PREFIX, {})).status_code == 404


async def test_the_rest_of_the_api_still_answers_on_a_public_host(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _deployed(monkeypatch, "enforce")
    for path in ("/api/v1/edition", "/healthz", "/api/v1/internalise"):
        answer = await _on(client, API, path, {})
        assert answer.status_code == (404 if path.endswith("internalise") else 200), path
    with structlog.testing.capture_logs() as logs:
        await _on(client, API, "/api/v1/internalise", {})
    assert not [line for line in logs if line["event"].startswith("internal_api.")]


async def test_a_run_door_is_served_even_under_a_public_name(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await _run(project, "run-guard-2")
    _deployed(
        monkeypatch,
        "enforce",
        CHOREGOS_INTERNAL_API_URL=f"https://{DOOR}",
        CHOREGOS_RUN_DOOR_HOSTS=json.dumps([DOOR]),
        CHOREGOS_PUBLIC_HOSTS=json.dumps(["*.example.com"]),
    )
    assert (await _on(client, DOOR, _network("run-guard-2"), token)).status_code == 200
    assert (await _on(client, f"{DOOR}:443", _network("run-guard-2"), token)).status_code == 200
    assert (await _on(client, API, _network("run-guard-2"), token)).status_code == 404
    assert (await _on(client, "other.example.com", _network("run-guard-2"), token)).status_code == 404


async def test_a_forwarded_public_host_is_refused_and_opens_nothing(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reverse proxy that rewrites `Host` (the console's relay of `/api/v1` does) says where the
    request came in with `X-Forwarded-Host`. A client may write the header: it never opens a door."""
    token = await _run(project, "run-guard-3")
    _deployed(monkeypatch, "enforce", CHOREGOS_RUN_DOOR_HOSTS=json.dumps([DOOR]))
    url = _network("run-guard-3")

    relayed = await _on(client, SERVICE, url, token, **{"X-Forwarded-Host": f"{CONSOLE}:443"})
    assert relayed.status_code == 404
    listed = await _on(client, SERVICE, url, token, **{"X-Forwarded-Host": f"{DOOR}, {CONSOLE}"})
    assert listed.status_code == 404
    forged = await _on(client, API, url, token, **{"X-Forwarded-Host": DOOR})
    assert forged.status_code == 404, "naming a run door in a header must not open the public host"
    assert (await _on(client, SERVICE, url, token, **{"X-Forwarded-Host": SERVICE})).status_code == 200


# ───────────────────────── warn, off, and the address runs are sent to ─────────────────────────


async def test_warn_serves_and_says_what_it_would_refuse(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await _run(project, "run-guard-4")
    _deployed(monkeypatch, "warn")
    with structlog.testing.capture_logs() as logs:
        on_service = await _on(client, SERVICE, _network("run-guard-4"), token)
        on_public = await _on(client, f"{API}:443", f"{_network('run-guard-4')}?x=1", token)
    assert (on_service.status_code, on_public.status_code) == (200, 200)
    said = [line for line in logs if line["event"].startswith("internal_api.")]
    assert said == [
        {
            "event": "internal_api.would_refuse",
            "log_level": "warning",
            "mode": "warn",
            "host": API,
            "header": "host",
            "path_prefix": f"{PREFIX}/runs",
        }
    ]
    assert token["Authorization"].split()[1] not in json.dumps(logs, default=str)


async def test_the_default_mode_is_warn(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await _run(project, "run-guard-5")
    monkeypatch.delenv("CHOREGOS_INTERNAL_API_GUARD", raising=False)
    _deployed(monkeypatch, None)
    assert Settings().internal_api_guard == "warn"
    with structlog.testing.capture_logs() as logs:
        assert (await _on(client, API, _network("run-guard-5"), token)).status_code == 200
    assert [line["event"] for line in logs if line["event"].startswith("internal_api.")] == [
        "internal_api.would_refuse"
    ]


async def test_off_checks_nothing(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    token = await _run(project, "run-guard-6")
    _deployed(monkeypatch, "off")
    with structlog.testing.capture_logs() as logs:
        assert (await _on(client, API, _network("run-guard-6"), token)).status_code == 200
    assert not [line for line in logs if line["event"].startswith("internal_api.")]


@pytest.mark.parametrize(
    "callback",
    [f"https://{API}", f"https://{API}/api/v1/internal", ""],
    ids=["the public API", "with the suffix", "unset, so the API's own URL"],
)
async def test_enforce_never_refuses_the_address_runs_are_sent_to(
    client: AsyncClient, project: dict[str, Any], monkeypatch: pytest.MonkeyPatch, callback: str
) -> None:
    """Runs outside the cluster call back over a public URL. With no run door declared, `enforce`
    would end every run at its first call: the guard warns instead, and says so at start-up."""
    token = await _run(project, "run-guard-7")
    _deployed(monkeypatch, "enforce", CHOREGOS_INTERNAL_API_URL=callback)
    with structlog.testing.capture_logs() as logs:
        answer = await _on(client, API, _network("run-guard-7"), token)
        announce(Settings())
    assert answer.status_code == 200, answer.text
    events = [line["event"] for line in logs if line["event"].startswith("internal_api.")]
    assert events == ["internal_api.would_refuse", "internal_api.guard", "internal_api.guard_not_enforced"]
    told = logs[-1]
    assert told["callback_host"] == API
    for name in ("CHOREGOS_RUN_DOOR_HOSTS", "CHOREGOS_INTERNAL_API_URL", "runDoor.hostnames"):
        assert name in told["detail"], f"the start-up line must name {name}"

    # The door declared and the runs sent to it: `enforce` applies again, and start-up says nothing more.
    _deployed(
        monkeypatch,
        "enforce",
        CHOREGOS_INTERNAL_API_URL=f"https://{DOOR}",
        CHOREGOS_RUN_DOOR_HOSTS=json.dumps([DOOR]),
    )
    with structlog.testing.capture_logs() as logs:
        announce(Settings())
        assert (await _on(client, API, _network("run-guard-7"), token)).status_code == 404
        assert (await _on(client, DOOR, _network("run-guard-7"), token)).status_code == 200
    assert [line["event"] for line in logs if line["event"].startswith("internal_api.")] == [
        "internal_api.guard",
        "internal_api.refused",
    ]


def test_a_development_machine_is_never_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """`make api`: the API's public URL and the callback are both `localhost:8000`."""
    for name in (
        "CHOREGOS_PUBLIC_URL",
        "CHOREGOS_API_URL",
        "CHOREGOS_CORS_ORIGINS",
        "CHOREGOS_INTERNAL_API_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("CHOREGOS_INTERNAL_API_GUARD", "enforce")
    guard = guard_of(Settings())
    assert guard.public == ("localhost",)
    assert guard.callback == "localhost"
    assert guard.held and not guard.refuses


def test_the_compose_install_is_not_held(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose: the console and the API are published on `localhost`, runs call the service `api`."""
    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", "http://localhost:3000")
    monkeypatch.setenv("CHOREGOS_API_URL", "http://localhost:8000")
    monkeypatch.setenv("CHOREGOS_CORS_ORIGINS", "http://localhost:3000")
    monkeypatch.setenv("CHOREGOS_INTERNAL_API_URL", "http://api:8000")
    monkeypatch.setenv("CHOREGOS_INTERNAL_API_GUARD", "enforce")
    guard = guard_of(Settings())
    assert not guard.held and guard.refuses
    assert guard.public_host([("host", "api")]) is None
    assert guard.public_host([("host", "api"), ("x-forwarded-host", "localhost")]) == (
        "x-forwarded-host",
        "localhost",
    )


# ───────────────────────── names ─────────────────────────


@pytest.mark.parametrize(
    ("written", "name"),
    [
        ("api.example.com", "api.example.com"),
        ("API.Example.com:8443", "api.example.com"),
        (" api.example.com. ", "api.example.com"),
        ("choregos-api:8000", "choregos-api"),
        ("[2001:db8::1]:8000", "2001:db8::1"),
        ("[::1]", "::1"),
        ("10.42.0.7:8000", "10.42.0.7"),
        ("", ""),
    ],
)
def test_a_host_is_compared_without_its_port_case_or_final_dot(written: str, name: str) -> None:
    assert hostname(written) == name


def test_every_line_of_the_two_headers_is_a_name_the_request_arrived_under() -> None:
    headers = Headers(
        raw=[
            (b"host", b"choregos-api:8000"),
            (b"host", b"API.example.com"),
            (b"x-forwarded-host", b"app.example.com:443, edge.example.net"),
            (b"x-forwarded-host", b""),
            (b"x-forwarded-host", b"second.example.com."),
            (b"x-forwarded-for", b"203.0.113.9"),
        ]
    )
    assert presented_hosts(headers) == [
        ("host", "choregos-api"),
        ("host", "api.example.com"),
        ("x-forwarded-host", "app.example.com"),
        ("x-forwarded-host", "edge.example.net"),
        ("x-forwarded-host", "second.example.com"),
    ]
    assert presented_hosts(Headers(raw=[])) == []


def test_a_wildcard_names_sub_domains_only() -> None:
    guard = Guard(
        mode="enforce", public=("*.example.com", "console.test"), doors=("*.runs.example.com",), callback=""
    )
    assert guard.public_host([("host", "a.example.com")]) == ("host", "a.example.com")
    assert guard.public_host([("host", "a.b.example.com")]) == ("host", "a.b.example.com")
    assert guard.public_host([("host", "example.com")]) is None
    assert guard.public_host([("host", "notexample.com")]) is None
    assert guard.public_host([("host", "eu.runs.example.com")]) is None, "a run door"
    assert guard.public_host([("host", "console.test")]) == ("host", "console.test")


# ───────────────────────── settings ─────────────────────────


def test_an_unknown_mode_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_INTERNAL_API_GUARD", "strict")
    with pytest.raises(ValueError, match="internal_api_guard"):
        Settings()


@pytest.mark.parametrize("name", ["CHOREGOS_RUN_DOOR_HOSTS", "CHOREGOS_PUBLIC_HOSTS"])
@pytest.mark.parametrize(
    "entry", ["https://runs.example.com", "runs.example.com:443", "runs.example.com/x", ""]
)
def test_a_host_setting_that_is_not_a_hostname_is_refused(
    monkeypatch: pytest.MonkeyPatch, name: str, entry: str
) -> None:
    """It would never equal a `Host` header, and nothing would say so."""
    monkeypatch.setenv(name, json.dumps([entry]))
    with pytest.raises(ValueError, match=name):
        Settings()


def test_the_host_settings_are_json_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_RUN_DOOR_HOSTS", '["runs.example.com", "*.runs.example.net"]')
    monkeypatch.setenv("CHOREGOS_PUBLIC_HOSTS", '["api.example.com", "*.example.org"]')
    monkeypatch.setenv("CHOREGOS_API_URL", "https://API.example.com:8443")
    monkeypatch.setenv("CHOREGOS_PUBLIC_URL", "https://app.example.com")
    monkeypatch.setenv("CHOREGOS_CORS_ORIGINS", "https://app.example.com, https://second.example.com")
    monkeypatch.setenv("CHOREGOS_INTERNAL_API_URL", "http://choregos-api.choregos.svc.cluster.local:8000")
    guard = guard_of(Settings())
    assert guard.doors == ("runs.example.com", "*.runs.example.net")
    assert guard.public == ("app.example.com", "api.example.com", "second.example.com", "*.example.org")
    assert guard.callback == "choregos-api.choregos.svc.cluster.local"
    assert not guard.held


# ───────────────────────── a socket under the prefix ─────────────────────────


def test_a_socket_under_the_prefix_is_guarded_as_a_request_is(monkeypatch: pytest.MonkeyPatch) -> None:
    """No route under the prefix is a WebSocket today; one added tomorrow is behind the same guard."""

    async def echo(socket: WebSocket) -> None:
        await socket.accept()
        await socket.send_text("served")
        await socket.close()

    _deployed(monkeypatch, "enforce")
    app = InternalApiGuard(Starlette(routes=[WebSocketRoute(f"{PREFIX}/echo", echo)]))
    with TestClient(app) as http:
        with http.websocket_connect(f"{PREFIX}/echo", headers={"Host": SERVICE}) as socket:
            assert socket.receive_text() == "served"
        with (
            pytest.raises(WebSocketDisconnect),
            http.websocket_connect(f"{PREFIX}/echo", headers={"Host": API}),
        ):
            pass
    reset_settings_cache()
