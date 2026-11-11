"""The chart tells the API where the run's API may answer, and gives outside runs a door (S65-02, ADR 0144).

`/api/v1/internal` is what a run calls with its run token. The API's route sends the whole `/api`
prefix of the public hostnames to the API, so the API itself refuses those routes there
(`choregos-api.internalApiGuard`). The chart passes it what it needs to decide: the mode, the
hostnames it routes, the run doors and the address runs are told to call back. With
`choregos-api.runDoor.hostnames`, it renders a second route that serves `/api/v1/internal` and
nothing else, under both of its entry forms.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
DOOR = "choregos-run-door"
DOOR_HOST = "runs.example.com"
DOOR_SET = f'choregos-api.runDoor.hostnames=["{DOOR_HOST}"]'
INGRESS = 'choregos-api.ingress.kind="ingress"'
PUBLIC_PATHS = ["/api", "/mcp", "/healthz", "/readyz", "/.well-known/oauth-protected-resource", "/a2a"]
GUARD, DOORS, PUBLIC, CALLBACK = (
    "CHOREGOS_INTERNAL_API_GUARD",
    "CHOREGOS_RUN_DOOR_HOSTS",
    "CHOREGOS_PUBLIC_HOSTS",
    "CHOREGOS_INTERNAL_API_URL",
)

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _render(*json_sets: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for value in json_sets:
        command += ["--set-json", value]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _documents(*json_sets: str) -> list[dict[str, Any]]:
    rendered = _render(*json_sets)
    assert rendered.returncode == 0, rendered.stderr[-600:]
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def _env(documents: list[dict[str, Any]], workload: str) -> dict[str, str]:
    (container,) = [
        container
        for doc in documents
        if doc["kind"] in {"Deployment", "Rollout"} and doc["metadata"]["name"] == workload
        for container in doc["spec"]["template"]["spec"]["containers"][:1]
    ]
    return {item["name"]: item.get("value", "") for item in container.get("env") or []}


def _named(documents: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any] | None:
    return next((d for d in documents if d["kind"] == kind and d["metadata"]["name"] == name), None)


def _ingress_paths(ingress: dict[str, Any]) -> dict[str, list[str]]:
    return {
        rule["host"]: [path["path"] for path in rule["http"]["paths"]] for rule in ingress["spec"]["rules"]
    }


def _route_prefixes(route: dict[str, Any]) -> list[str]:
    return [match["path"]["value"] for rule in route["spec"]["rules"] for match in rule["matches"]]


# ───────────────────────── what the API is told ─────────────────────────


def test_the_api_warns_by_default_and_knows_the_names_it_is_routed_under(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    documents = _documents()
    env = _env(documents, "choregos-api")
    route = _named(documents, "HTTPRoute", "choregos-api")
    assert route is not None
    assert env[GUARD] == "warn"
    assert json.loads(env[DOORS]) == []
    assert json.loads(env[PUBLIC]) == route["spec"]["hostnames"], "every routed hostname is a public one"

    # What the chart writes is what the API's settings accept, and it leaves the guard free to refuse:
    # runs are sent to the Service, which is no public name.
    from choregos_api.config import Settings
    from choregos_api.internal_api_guard import guard_of

    for name in (
        GUARD,
        DOORS,
        PUBLIC,
        CALLBACK,
        "CHOREGOS_PUBLIC_URL",
        "CHOREGOS_API_URL",
        "CHOREGOS_CORS_ORIGINS",
    ):
        monkeypatch.setenv(name, env[name])
    guard = guard_of(Settings())
    assert set(route["spec"]["hostnames"]) <= set(guard.public)
    assert guard.callback == "choregos-api"
    assert not guard.held


def test_the_api_and_the_workers_agree_on_where_runs_call_back() -> None:
    """The guard never refuses the address runs are sent to: it must be the workers' own value."""
    for sets in ((), (f'global.internalApiUrl="https://{DOOR_HOST}"',)):
        documents = _documents(*sets)
        workers = [
            _env(documents, doc["metadata"]["name"])[CALLBACK]
            for doc in documents
            if doc["kind"] == "Deployment" and doc["metadata"]["name"].startswith("choregos-orchestrator")
        ]
        assert workers, "no worker deployment rendered: the test checks nothing"
        assert set(workers) == {_env(documents, "choregos-api")[CALLBACK]}, sets


def test_the_mode_the_door_and_the_extra_public_names_reach_the_api() -> None:
    env = _env(
        _documents(
            'choregos-api.internalApiGuard="enforce"',
            DOOR_SET,
            'choregos-api.publicHostnames=["*.apps.example.com"]',
            'choregos-api.ingress.hostnames=["api.example.com"]',
        ),
        "choregos-api",
    )
    assert env[GUARD] == "enforce"
    assert json.loads(env[DOORS]) == [DOOR_HOST]
    assert json.loads(env[PUBLIC]) == ["api.example.com", "*.apps.example.com"]


def test_a_route_made_outside_the_chart_is_not_guessed() -> None:
    env = _env(_documents("choregos-api.ingress.enabled=false"), "choregos-api")
    assert json.loads(env[PUBLIC]) == [], "without its own route the chart knows no hostname"


def test_an_unknown_mode_fails_the_render() -> None:
    rendered = _render('choregos-api.internalApiGuard="strict"')
    assert rendered.returncode != 0
    assert "choregos-api.internalApiGuard" in rendered.stderr


@pytest.mark.parametrize(
    ("name", "value"),
    [
        (GUARD, "choregos-api.internalApiGuard"),
        (DOORS, "choregos-api.runDoor.hostnames"),
        (PUBLIC, "choregos-api.publicHostnames"),
    ],
)
def test_extra_env_cannot_shadow_the_guard(name: str, value: str) -> None:
    rendered = _render(f'global.extraEnv=[{{"name":"{name}","value":"off"}}]')
    assert rendered.returncode != 0, f"the chart rendered two values for {name}"
    assert value in rendered.stderr, "the error says where to set it instead"


# ───────────────────────── the run door ─────────────────────────


@pytest.mark.parametrize("form", [(), (INGRESS,)], ids=["httproute", "ingress"])
def test_no_door_is_rendered_until_one_is_asked_for(form: tuple[str, ...]) -> None:
    documents = _documents(*form)
    assert not [d for d in documents if d["metadata"]["name"] == DOOR]


def test_the_door_ingress_serves_the_runs_api_and_nothing_else() -> None:
    documents = _documents(
        INGRESS,
        'choregos-api.ingress.className="nginx"',
        'choregos-api.ingress.annotations={"example.com/of-the-api":"yes"}',
        f'choregos-api.runDoor={{"hostnames":["{DOOR_HOST}"],"annotations":{{"example.com/of-the-door":"yes"}}}}',
    )
    door, public = _named(documents, "Ingress", DOOR), _named(documents, "Ingress", "choregos-api")
    assert door is not None and public is not None
    assert _ingress_paths(door) == {DOOR_HOST: ["/api/v1/internal"]}
    (path,) = door["spec"]["rules"][0]["http"]["paths"]
    assert path["pathType"] == "Prefix"
    assert path["backend"]["service"] == {"name": "choregos-api", "port": {"number": 8000}}
    assert door["spec"]["ingressClassName"] == "nginx"
    # Its annotations are its own: a source-address or rate rule on the door must not land on the API's.
    assert door["metadata"]["annotations"] == {"example.com/of-the-door": "yes"}
    assert public["metadata"]["annotations"] == {"example.com/of-the-api": "yes"}
    # The public route is as it was: the API's guard closes it, not the route.
    assert DOOR_HOST not in _ingress_paths(public)
    assert all(paths == PUBLIC_PATHS for paths in _ingress_paths(public).values())


def test_the_door_http_route_serves_the_runs_api_and_nothing_else() -> None:
    documents = _documents(DOOR_SET)
    door, public = _named(documents, "HTTPRoute", DOOR), _named(documents, "HTTPRoute", "choregos-api")
    assert door is not None and public is not None
    assert door["spec"]["hostnames"] == [DOOR_HOST]
    assert _route_prefixes(door) == ["/api/v1/internal"]
    (rule,) = door["spec"]["rules"]
    assert rule["matches"][0]["path"]["type"] == "PathPrefix"
    assert rule["backendRefs"] == [{"name": "choregos-api", "port": 8000}]
    assert door["spec"]["parentRefs"] == public["spec"]["parentRefs"]
    assert DOOR_HOST not in public["spec"]["hostnames"]
    assert _route_prefixes(public) == PUBLIC_PATHS


@pytest.mark.parametrize("form", [(), (INGRESS,)], ids=["httproute", "ingress"])
def test_a_door_that_is_a_public_hostname_fails_the_render(form: tuple[str, ...]) -> None:
    rendered = _render(
        *form,
        'choregos-api.ingress.hostnames=["api.example.com"]',
        'choregos-api.runDoor.hostnames=["api.example.com"]',
    )
    assert rendered.returncode != 0, "a door on the public hostname would keep the run's API there"
    assert "choregos-api.runDoor.hostnames" in rendered.stderr
