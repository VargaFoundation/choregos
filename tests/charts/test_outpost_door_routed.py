"""The outposts' door in the platform's chart: its switch, its route, its key, its own Deployment
(S65-16, ADR 0148 §7, §8 and §13).

An outpost dials one hostname, the door's, and nothing of the platform ever dials an outpost. The
chart therefore renders an entrance and no way out: with `global.outposts.enabled` off, nothing;
on, the API and the workers are told, the door's own hostnames are routed to `/outposts` and to
nothing else under both entry forms, the door has a signing key that no upgrade can change, and
`global.outposts.door.dedicated` gives it a Deployment that serves the door alone.

Each test renders the chart with `helm template` and reads the objects. Where the chart hands a
process its settings, the process's own code reads them back: the API's `Settings`, and the
application of `python -m choregos_api.outposts`.
"""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml

CHART = pathlib.Path(__file__).resolve().parents[2] / "charts" / "choregos"
DOOR = "choregos-outposts-door"
DOOR_HOST = "outposts.example.com"
ON = "global.outposts.enabled=true"
HOSTS = f'global.outposts.door.hostnames=["{DOOR_HOST}"]'
DEDICATED = "global.outposts.door.dedicated=true"
INGRESS = 'choregos-api.ingress.kind="ingress"'
#: What every process that may place a call is given, and what only the door's processes are.
COMMON = {
    "CHOREGOS_OUTPOSTS",
    "CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_OUTPOST",
    "CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_ORGANISATION",
}
OF_THE_DOOR = {
    "CHOREGOS_OUTPOSTS_DOOR_URL",
    "CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY",
    "CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS",
    "CHOREGOS_OUTPOSTS_DOOR_RATE_PER_MINUTE",
}
#: The public route's paths, in the chart's order (`test_run_door.py`): none of them is the door's.
PUBLIC_PATHS = [
    "/api",
    "/mcp",
    "/healthz",
    "/readyz",
    "/.well-known/oauth-protected-resource",
    "/a2a",
    "/downloads",
    "/cli/install.sh",
    "/cli/install.ps1",
]

pytestmark = pytest.mark.skipif(shutil.which("helm") is None, reason="helm absent")


def _public_key(label: str) -> str:
    """The public half of an Ed25519 key derived from a label, as the contract writes a key: no key
    is written in this file."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(hashlib.sha256(label.encode()).digest())
    raw = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _render(*json_sets: str) -> subprocess.CompletedProcess[str]:
    command = ["helm", "template", "choregos", str(CHART)]
    for value in json_sets:
        command += ["--set-json", value]
    return subprocess.run(command, capture_output=True, text=True, check=False)


def _documents(*json_sets: str) -> list[dict[str, Any]]:
    rendered = _render(*json_sets)
    assert rendered.returncode == 0, rendered.stderr[-800:]
    return [doc for doc in yaml.safe_load_all(rendered.stdout) if doc]


def _refusal(*json_sets: str) -> str:
    rendered = _render(*json_sets)
    assert rendered.returncode != 0, "the chart rendered what it must refuse"
    return rendered.stderr


def _named(documents: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any] | None:
    return next((d for d in documents if d["kind"] == kind and d["metadata"]["name"] == name), None)


def _workloads(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Every pod specification of the render, by the name of what carries it."""
    found: dict[str, dict[str, Any]] = {}
    for doc in documents:
        spec = doc.get("spec") or {}
        template = spec.get("jobTemplate", {}).get("spec", {}).get("template") or spec.get("template")
        if isinstance(template, dict) and "containers" in (template.get("spec") or {}):
            found[f"{doc['kind']}/{doc['metadata']['name']}"] = template["spec"]
    return found


def _entries(pod: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The variables of a pod's first container, each as it is written (`value` or `valueFrom`)."""
    return {item["name"]: item for item in pod["containers"][0].get("env") or []}


def _env(documents: list[dict[str, Any]], workload: str) -> dict[str, dict[str, Any]]:
    return _entries(_workloads(documents)[workload])


def _text(documents: list[dict[str, Any]]) -> str:
    return yaml.safe_dump_all(documents)


def _ingress_paths(ingress: dict[str, Any]) -> dict[str, list[str]]:
    return {
        rule["host"]: [path["path"] for path in rule["http"]["paths"]] for rule in ingress["spec"]["rules"]
    }


def _route_prefixes(route: dict[str, Any]) -> list[str]:
    """The path prefixes a route names; a rule without `matches` (the console's) catches every path."""
    return [match["path"]["value"] for rule in route["spec"]["rules"] for match in rule.get("matches", [])]


# ───────────────────────── disabled: nothing of the door ─────────────────────────


@pytest.mark.parametrize("form", [(), (INGRESS,)], ids=["httproute", "ingress"])
def test_disabled_nothing_of_the_door_is_rendered(form: tuple[str, ...]) -> None:
    """Off is the default, and off wins over everything else that is set about the door."""
    for sets in ((), (HOSTS, DEDICATED, 'global.outposts.door.url="https://door.example.com"')):
        documents = _documents(*form, *sets)
        assert not [d["metadata"]["name"] for d in documents if "outposts" in d["metadata"]["name"]]
        assert "outpost" not in _text(documents).lower(), "something of the outposts was rendered"
        for name, pod in _workloads(documents).items():
            assert not [variable for variable in _entries(pod) if "OUTPOST" in variable], name


def test_disabled_a_value_of_the_door_changes_nothing_of_the_render() -> None:
    """Not a byte, the API pod's checksum included: a door nobody runs restarts no API."""
    plain = _render().stdout
    assert _render('global.outposts.door.url="https://door.example.com"', DEDICATED).stdout == plain
    assert _render("global.outposts.callsInFlight.perOutpost=4").stdout == plain


def test_enabled_a_value_of_the_door_redeploys_the_api() -> None:
    """A rotation that announces a key must reach the pods: the checksum covers the door once it is on."""

    def checksum(*sets: str) -> str:
        api = _named(_documents(ON, HOSTS, *sets), "Deployment", "choregos-api")
        assert api is not None
        return str(api["spec"]["template"]["metadata"]["annotations"]["checksum/config"])

    assert checksum() != checksum("global.outposts.door.ratePerMinute=600")


# ───────────────────────── enabled: who is told what ─────────────────────────


def test_the_api_is_told_to_serve_the_door_with_its_origin_and_its_key() -> None:
    env = _env(_documents(ON, HOSTS), "Deployment/choregos-api")
    assert env["CHOREGOS_OUTPOSTS"]["value"] == "on"
    # The origin is derived from the door's first hostname: what an operator writes as `control_plane`.
    assert env["CHOREGOS_OUTPOSTS_DOOR_URL"]["value"] == f"https://{DOOR_HOST}"
    # The key is read from its Secret by reference and is NOT optional: a door that signs nothing
    # hands nothing over, so a missing key must stop the pod, visibly.
    assert env["CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY"]["valueFrom"] == {
        "secretKeyRef": {"name": DOOR, "key": "signing-key"}
    }
    assert "value" not in env["CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY"]
    assert json.loads(env["CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS"]["value"]) == []
    assert env["CHOREGOS_OUTPOSTS_DOOR_RATE_PER_MINUTE"]["value"] == "1200"
    assert env["CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_OUTPOST"]["value"] == "128"
    assert env["CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_ORGANISATION"]["value"] == "512"


def test_the_values_reach_the_variables_the_api_reads() -> None:
    announced = _public_key("choregos tests: the door's next key, announced for a rotation")
    env = _env(
        _documents(
            ON,
            'global.outposts.door.url="https://door.example.com"',
            f'global.outposts.door.announcedKeys=["{announced}"]',
            "global.outposts.door.ratePerMinute=0",
            'global.outposts.callsInFlight={"perOutpost":16,"perOrganisation":64}',
            'global.outposts.door.signingKey={"existingSecret":"door-key","key":"ed25519.pem"}',
        ),
        "Deployment/choregos-api",
    )
    assert env["CHOREGOS_OUTPOSTS_DOOR_URL"]["value"] == "https://door.example.com"
    assert json.loads(env["CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS"]["value"]) == [announced]
    assert env["CHOREGOS_OUTPOSTS_DOOR_RATE_PER_MINUTE"]["value"] == "0", "0 lifts the limit: it is a value"
    assert env["CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_OUTPOST"]["value"] == "16"
    assert env["CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_ORGANISATION"]["value"] == "64"
    assert env["CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY"]["valueFrom"]["secretKeyRef"] == {
        "name": "door-key",
        "key": "ed25519.pem",
    }


def _settings_of(
    monkeypatch: pytest.MonkeyPatch, documents: list[dict[str, Any]], workload: str
) -> dict[str, str]:
    """Give this process what the chart gives `workload`: its plain values, and the door's key read
    from the Secret the chart drew. The database is left out: no test here opens it."""
    from choregos_api.config import reset_settings_cache

    secret = _named(documents, "Secret", DOOR)
    assert secret is not None
    given: dict[str, str] = {}
    for name, entry in _env(documents, workload).items():
        if name.startswith("CHOREGOS_DATABASE") or name in {"CHOREGOS_ENV", "CHOREGOS_PORT"}:
            continue
        if "value" in entry:
            given[name] = entry["value"]
        elif entry["valueFrom"].get("secretKeyRef", {}).get("name") == DOOR:
            given[name] = base64.b64decode(secret["data"][entry["valueFrom"]["secretKeyRef"]["key"]]).decode()
    for name, value in given.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("CHOREGOS_DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    reset_settings_cache()
    return given


def test_what_the_chart_writes_is_what_the_api_accepts(monkeypatch: pytest.MonkeyPatch) -> None:
    """The API refuses to start on a door without its origin or its key, on an origin with a path and
    on a key that is not Ed25519 in PEM: its own settings read what the chart rendered."""
    from choregos_api.config import Settings, reset_settings_cache
    from choregos_api.outposts import check_the_door

    announced = _public_key("choregos tests: the door's next key, announced for a rotation")
    documents = _documents(ON, HOSTS, f'global.outposts.door.announcedKeys=["{announced}"]')
    _settings_of(monkeypatch, documents, "Deployment/choregos-api")
    try:
        settings = Settings()
        assert settings.outposts is True
        assert settings.outposts_door_origin == f"https://{DOOR_HOST}"
        check_the_door(settings)
        own, *others = settings.outposts_door_keys
        assert len(own) == 43 and others == [announced]
        assert settings.outposts_door_rate_per_minute == 1200
        assert settings.outposts_calls_in_flight_per_outpost == 128
    finally:
        monkeypatch.undo()
        reset_settings_cache()


def test_the_workers_learn_the_switch_and_the_quotas_and_never_the_doors_key() -> None:
    """Whoever places a call reads the switch and the quotas (`rendezvous.place`): the API for a read,
    the workers for a governed write. Only the door's processes sign a call."""
    documents = _documents(ON, HOSTS)
    workers = {
        name: _entries(pod)
        for name, pod in _workloads(documents).items()
        if name.startswith("Deployment/choregos-orchestrator")
    }
    assert workers, "no worker deployment rendered: the test checks nothing"
    for name, env in workers.items():
        assert env["CHOREGOS_OUTPOSTS"]["value"] == "on", name
        assert set(env) >= COMMON, name
        assert not OF_THE_DOOR & set(env), f"{name} receives the door's own settings"
    # And nothing else of the release does, the API apart.
    for name, pod in _workloads(documents).items():
        if name != "Deployment/choregos-api":
            assert "CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY" not in _entries(pod), name


@pytest.mark.parametrize("name", sorted(COMMON | OF_THE_DOOR))
def test_extra_env_cannot_shadow_what_the_chart_sets(name: str) -> None:
    former = f'global.extraEnv=[{{"name":"{name}","value":"x"}}]'
    refusal = _refusal(ON, HOSTS, former)
    assert name in refusal, "the error names the variable"
    # Until the switch is on, the former way (the settings through `extraEnv`) still renders.
    assert _render(former).returncode == 0


# ───────────────────────── the origin: named, or the render fails first ─────────────────────────


def test_enabled_without_an_origin_fails_the_render_and_names_the_value() -> None:
    refusal = _refusal(ON)
    assert "global.outposts.door.hostnames" in refusal and "global.outposts.door.url" in refusal


def test_an_origin_with_a_path_fails_the_render() -> None:
    refusal = _refusal(ON, 'global.outposts.door.url="https://door.example.com/outposts"')
    assert "global.outposts.door.url" in refusal and "not an origin" in refusal


def test_the_origin_may_differ_from_the_hostnames_the_chart_routes() -> None:
    """Behind an entrance of the operator's own: the public name is given, the route follows the hostnames."""
    documents = _documents(ON, HOSTS, 'global.outposts.door.url="https://door.example.net"')
    env = _env(documents, "Deployment/choregos-api")
    assert env["CHOREGOS_OUTPOSTS_DOOR_URL"]["value"] == "https://door.example.net"
    route = _named(documents, "HTTPRoute", DOOR)
    assert route is not None and route["spec"]["hostnames"] == [DOOR_HOST]


@pytest.mark.parametrize(
    ("announced", "said"),
    [
        ('["not-a-key"]', "not a public key"),
        ('["{k}","{k}"]', "announced once"),
        ('["{a}","{b}","{c}","{d}"]', "at most three"),
    ],
    ids=["malformed", "twice", "a fourth"],
)
def test_announced_keys_the_api_would_refuse_fail_the_render(announced: str, said: str) -> None:
    def key(letter: str) -> str:
        return letter * 42 + "A"

    value = announced.format(k=key("k"), a=key("a"), b=key("b"), c=key("c"), d=key("d"))
    refusal = _refusal(ON, HOSTS, f"global.outposts.door.announcedKeys={value}")
    assert "global.outposts.door.announcedKeys" in refusal and said in refusal


# ───────────────────────── the route ─────────────────────────


def test_the_http_route_serves_outposts_under_the_doors_hostnames_only() -> None:
    documents = _documents(ON, HOSTS)
    door = _named(documents, "HTTPRoute", DOOR)
    public = _named(documents, "HTTPRoute", "choregos-api")
    console = _named(documents, "HTTPRoute", "choregos-web")
    assert door is not None and public is not None and console is not None
    assert door["spec"]["hostnames"] == [DOOR_HOST]
    (rule,) = door["spec"]["rules"]
    assert rule["matches"] == [{"path": {"type": "PathPrefix", "value": "/outposts"}}]
    # Not split out: the API serves the door.
    assert rule["backendRefs"] == [{"name": "choregos-api", "port": 8000}]
    assert door["spec"]["parentRefs"] == public["spec"]["parentRefs"]
    # The door's path is served nowhere else, and the door's hostname is nobody else's: the console's
    # route catches every path, under the console's hostnames only.
    assert _route_prefixes(public) == PUBLIC_PATHS
    for other in (public, console):
        assert DOOR_HOST not in other["spec"]["hostnames"]
        assert not [prefix for prefix in _route_prefixes(other) if prefix.startswith("/outposts")]
    routes = [d for d in documents if d["kind"] == "HTTPRoute"]
    assert [d["metadata"]["name"] for d in routes if DOOR_HOST in d["spec"]["hostnames"]] == [DOOR]


def test_the_ingress_serves_outposts_under_the_doors_hostnames_only() -> None:
    second = "outposts.example.org"
    documents = _documents(
        ON,
        INGRESS,
        'choregos-web.ingress.kind="ingress"',
        'choregos-api.ingress.className="nginx"',
        'choregos-api.ingress.annotations={"example.com/of-the-api":"yes"}',
        f'global.outposts.door.hostnames=["{DOOR_HOST}","{second}"]',
    )
    door = _named(documents, "Ingress", DOOR)
    public = _named(documents, "Ingress", "choregos-api")
    assert door is not None and public is not None
    assert _ingress_paths(door) == {DOOR_HOST: ["/outposts"], second: ["/outposts"]}
    for rule in door["spec"]["rules"]:
        (path,) = rule["http"]["paths"]
        assert path["pathType"] == "Prefix"
        assert path["backend"]["service"] == {"name": "choregos-api", "port": {"number": 8000}}
    assert door["spec"]["ingressClassName"] == "nginx"
    # The first hostname is the origin.
    assert _env(documents, "Deployment/choregos-api")["CHOREGOS_OUTPOSTS_DOOR_URL"]["value"] == (
        f"https://{DOOR_HOST}"
    )
    assert public["metadata"]["annotations"] == {"example.com/of-the-api": "yes"}
    for ingress in (d for d in documents if d["kind"] == "Ingress" and d["metadata"]["name"] != DOOR):
        hosts = _ingress_paths(ingress)
        assert not {DOOR_HOST, second} & set(hosts), ingress["metadata"]["name"]
        assert not [p for paths in hosts.values() for p in paths if p.startswith("/outposts")]


def test_the_entrance_lets_a_poll_wait_its_twenty_five_seconds() -> None:
    """A poll waits up to 25 seconds before it answers with nothing (ADR 0148 §7): the HTTPRoute says
    how long a request may last, the Ingress carries the door's own annotations for it."""
    from choregos_contracts.outpost import MAX_WAIT_S

    route = _named(_documents(ON, HOSTS), "HTTPRoute", DOOR)
    assert route is not None
    timeout = route["spec"]["rules"][0]["timeouts"]["request"]
    assert timeout.endswith("s") and int(timeout[:-1]) > MAX_WAIT_S
    # A Gateway API whose HTTPRoute has no `timeouts` yet: the field can be left out.
    older = _named(_documents(ON, HOSTS, 'global.outposts.door.requestTimeout=""'), "HTTPRoute", DOOR)
    assert older is not None and "timeouts" not in older["spec"]["rules"][0]

    ingress = _named(_documents(ON, HOSTS, INGRESS), "Ingress", DOOR)
    assert ingress is not None
    annotations = ingress["metadata"]["annotations"]
    assert int(annotations["nginx.ingress.kubernetes.io/proxy-read-timeout"]) > MAX_WAIT_S
    assert annotations["nginx.ingress.kubernetes.io/proxy-buffering"] == "off"
    # They are the door's own: another controller's, a rate limit, a source restriction go there too.
    own = _named(
        _documents(ON, HOSTS, INGRESS, 'global.outposts.door.annotations={"example.com/limit":"10r/s"}'),
        "Ingress",
        DOOR,
    )
    assert own is not None and own["metadata"]["annotations"]["example.com/limit"] == "10r/s"


def test_a_route_made_outside_the_chart_is_not_doubled() -> None:
    """Where the API's route is the operator's own, so is the door's: the API is told all the same."""
    documents = _documents(ON, HOSTS, "choregos-api.ingress.enabled=false")
    assert not [
        d for d in documents if d["kind"] in {"HTTPRoute", "Ingress"} and d["metadata"]["name"] == DOOR
    ]
    assert _env(documents, "Deployment/choregos-api")["CHOREGOS_OUTPOSTS"]["value"] == "on"


@pytest.mark.parametrize(
    "form", [(), (INGRESS, 'choregos-web.ingress.kind="ingress"')], ids=["httproute", "ingress"]
)
@pytest.mark.parametrize(
    ("taken", "by"),
    [
        (('choregos-web.ingress.hostnames=["{host}"]',), "choregos-web.ingress.hostnames"),
        (('global.publicUrl="https://{host}"',), "global.publicUrl"),
        (('choregos-api.ingress.hostnames=["{host}"]',), "choregos-api.ingress.hostnames"),
        (('choregos-api.runDoor.hostnames=["{host}"]',), "choregos-api.runDoor.hostnames"),
    ],
    ids=["the console's", "the console's public URL", "the API's", "a run door's"],
)
def test_a_door_hostname_that_is_also_another_entrance_fails_the_render(
    form: tuple[str, ...], taken: tuple[str, ...], by: str
) -> None:
    """ADR 0148 §8: the one name an outpost's firewall opens is not the console's."""
    refusal = _refusal(ON, HOSTS, *form, *(value.format(host=DOOR_HOST) for value in taken))
    assert DOOR_HOST in refusal and by in refusal and "global.outposts.door" in refusal


def test_the_default_console_hostname_is_refused_as_a_door() -> None:
    """With nothing else set: the door asked for under the chart's own console hostname."""
    refusal = _refusal(ON, 'global.outposts.door.hostnames=["app.choregos.local"]')
    assert "the console's" in refusal
    # The origin counts too, when it is given without a hostname to route.
    refusal = _refusal(ON, 'global.outposts.door.url="https://APP.choregos.local"')
    assert "the console's" in refusal


# ───────────────────────── the signing key ─────────────────────────


def _key_secrets(documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [d for d in documents if d["kind"] == "Secret" and "outposts" in d["metadata"]["name"]]


def test_the_chart_draws_a_key_the_door_can_sign_with() -> None:
    from choregos_api.config import ed25519_public_key

    (secret,) = _key_secrets(_documents(ON, HOSTS))
    assert secret["metadata"]["name"] == DOOR and secret["type"] == "Opaque"
    pem = base64.b64decode(secret["data"]["signing-key"]).decode()
    # PEM, PKCS#8, Ed25519: what `CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY` takes, read by the API's own check.
    assert pem.startswith("-----BEGIN PRIVATE KEY-----")
    assert len(ed25519_public_key(pem)) == 43
    # Drawn, not written: another render has another key, so none is in the chart.
    (again,) = _key_secrets(_documents(ON, HOSTS))
    assert again["data"]["signing-key"] != secret["data"]["signing-key"]


def test_the_drawn_key_cannot_be_changed_by_an_upgrade_nor_deleted_by_an_uninstall() -> None:
    """Every outpost pins the public half of this key. A render that cannot read the cluster (Argo CD,
    `helm template | kubectl apply`) draws a new key each time: the Secret is immutable, so the apply
    is refused instead of the key changing under every outpost; and it is kept on uninstall."""
    (secret,) = _key_secrets(_documents(ON, HOSTS))
    assert secret["immutable"] is True
    assert secret["metadata"]["annotations"]["helm.sh/resource-policy"] == "keep"


def test_the_template_reads_the_key_back_before_it_draws_one() -> None:
    """What only a cluster shows at run time, held here in the template: the key is looked up in the
    release's namespace first, as the chart's other generated secrets are, and drawn only when the
    look-up finds none."""
    source = (CHART / "templates" / "outposts-door.yaml").read_text(encoding="utf-8")
    look_up = source.index('lookup "v1" "Secret" .Release.Namespace $name')
    read_back = source.index("$pem = index ($existing.data | default dict) $entry")
    drawn = source.index('{{- if not $pem }}{{- $pem = genPrivateKey "ed25519" | b64enc }}')
    assert look_up < read_back < drawn


def test_an_existing_secret_is_used_and_none_is_drawn() -> None:
    documents = _documents(
        ON, HOSTS, DEDICATED, 'global.outposts.door.signingKey={"existingSecret":"door-key","key":"k.pem"}'
    )
    assert _key_secrets(documents) == []
    for workload in ("Deployment/choregos-api", f"Deployment/{DOOR}"):
        reference = _env(documents, workload)["CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY"]["valueFrom"]
        assert reference == {"secretKeyRef": {"name": "door-key", "key": "k.pem"}}, workload


# ───────────────────────── dedicated: a Deployment serves the door alone ─────────────────────────


def test_not_dedicated_the_api_serves_the_door_and_nothing_else_is_deployed() -> None:
    documents = _documents(ON, HOSTS)
    assert not [d for d in documents if d["kind"] in {"Deployment", "Service", "PodDisruptionBudget"}
                and d["metadata"]["name"] == DOOR]  # fmt: skip
    assert not [d for d in documents if d["kind"] == "NetworkPolicy" and "outposts" in d["metadata"]["name"]]


def test_dedicated_a_deployment_serves_the_door_alone() -> None:
    documents = _documents(ON, HOSTS, DEDICATED)
    door, api = _named(documents, "Deployment", DOOR), _named(documents, "Deployment", "choregos-api")
    assert door is not None and api is not None
    pod = door["spec"]["template"]["spec"]
    (container,) = pod["containers"]
    # The API image, started as the entrypoint that mounts the door and nothing else of the API.
    assert container["image"] == api["spec"]["template"]["spec"]["containers"][0]["image"]
    assert container["command"] == ["python", "-m", "choregos_api.outposts"]
    assert importlib.util.find_spec("choregos_api.outposts.__main__") is not None
    assert door["spec"]["replicas"] == 2
    assert container["readinessProbe"]["httpGet"] == {"path": "/readyz", "port": "http"}
    assert container["livenessProbe"]["httpGet"] == {"path": "/healthz", "port": "http"}
    assert container["ports"] == [{"name": "http", "containerPort": 8000}]
    # Locked down as the platform's other pods, and it asks nothing of the cluster.
    assert pod["automountServiceAccountToken"] is False
    assert pod["securityContext"]["runAsNonRoot"] is True
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    assert container["resources"]["limits"] and container["resources"]["requests"]
    service = _named(documents, "Service", DOOR)
    assert service is not None
    assert service["spec"]["selector"] == door["spec"]["selector"]["matchLabels"]
    assert service["spec"]["ports"] == [{"name": "http", "port": 8000, "targetPort": "http"}]
    budget = _named(documents, "PodDisruptionBudget", DOOR)
    assert budget is not None and budget["spec"]["selector"] == door["spec"]["selector"]
    # The API's metrics are scraped by its component label: the door's Service must not answer to it.
    assert service["metadata"]["labels"]["app.kubernetes.io/component"] == "outposts-door"


@pytest.mark.parametrize("form", [(), (INGRESS,)], ids=["httproute", "ingress"])
def test_dedicated_the_route_leads_to_the_doors_own_service(form: tuple[str, ...]) -> None:
    documents = _documents(ON, HOSTS, DEDICATED, *form)
    if form:
        ingress = _named(documents, "Ingress", DOOR)
        assert ingress is not None
        (path,) = ingress["spec"]["rules"][0]["http"]["paths"]
        assert path["backend"]["service"] == {"name": DOOR, "port": {"number": 8000}}
    else:
        route = _named(documents, "HTTPRoute", DOOR)
        assert route is not None
        assert route["spec"]["rules"][0]["backendRefs"] == [{"name": DOOR, "port": 8000}]


def test_dedicated_the_door_has_its_own_bounded_pool() -> None:
    """Polls waiting by the hundred take no connection from the console and the runs (ADR 0148 §8)."""
    documents = _documents(ON, HOSTS, DEDICATED, 'global.outposts.door.pool={"size":3,"maxOverflow":0}')
    door, api = _env(documents, f"Deployment/{DOOR}"), _env(documents, "Deployment/choregos-api")
    assert door["CHOREGOS_DB_POOL_SIZE"]["value"] == "3"
    assert door["CHOREGOS_DB_MAX_OVERFLOW"]["value"] == "0", "no overflow is a bound too"
    assert door["CHOREGOS_DB_POOL_TIMEOUT_S"]["value"] == "10"
    assert (api["CHOREGOS_DB_POOL_SIZE"]["value"], api["CHOREGOS_DB_MAX_OVERFLOW"]["value"]) == ("10", "5")
    defaults = _env(_documents(ON, HOSTS, DEDICATED), f"Deployment/{DOOR}")
    assert (defaults["CHOREGOS_DB_POOL_SIZE"]["value"], defaults["CHOREGOS_DB_MAX_OVERFLOW"]["value"]) == (
        "5",
        "5",
    )


@pytest.mark.parametrize(
    "database",
    [(), ('global.database.passwordSecret="pg-credentials"',), ("global.database.embedded=true",)],
    ids=["a url secret", "a password secret", "embedded"],
)
def test_dedicated_the_door_reaches_the_database_the_api_reaches(database: tuple[str, ...]) -> None:
    documents = _documents(ON, HOSTS, DEDICATED, *database)
    door, api = _env(documents, f"Deployment/{DOOR}"), _env(documents, "Deployment/choregos-api")
    names = [name for name in api if name.startswith("CHOREGOS_DATABASE")]
    assert "CHOREGOS_DATABASE_URL" in names
    assert {name: door.get(name) for name in names} == {name: api[name] for name in names}


def test_dedicated_the_door_holds_what_it_needs_and_no_other_secret() -> None:
    """New with this Deployment: a database credential outside the API (ADR 0148 §8). It comes with the
    door's key and the key a call's arguments wait under, and with nothing else of the platform's."""
    connector = (
        '{"name":"CONNECTOR_TRACKER_TOKEN","valueFrom":{"secretKeyRef":{"name":"tracker","key":"token"}}}'
    )
    documents = _documents(ON, HOSTS, DEDICATED, f"global.extraEnv=[{connector}]")
    door, api = _env(documents, f"Deployment/{DOOR}"), _env(documents, "Deployment/choregos-api")
    assert set(door) >= (COMMON | OF_THE_DOOR)
    assert door["CHOREGOS_OUTPOSTS_DOOR_URL"] == api["CHOREGOS_OUTPOSTS_DOOR_URL"]
    assert door["CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY"] == api["CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY"]
    assert door["CHOREGOS_SECRETS_KEKS"] == api["CHOREGOS_SECRETS_KEKS"]
    assert door["CHOREGOS_SECRETS_KEKS"]["valueFrom"]["secretKeyRef"]["optional"] is True
    assert door["CHOREGOS_ENV"] == api["CHOREGOS_ENV"]
    secrets = {name for name, entry in door.items() if "secretKeyRef" in entry.get("valueFrom", {})}
    assert secrets == {"CHOREGOS_DATABASE_URL", "CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY", "CHOREGOS_SECRETS_KEKS"}
    for never in (
        "CHOREGOS_SESSION_SECRET",
        "CHOREGOS_RUN_TOKEN_PRIVATE_KEY",
        "CHOREGOS_GATEWAY_MASTER_KEY",
        "CHOREGOS_OIDC_CLIENT_SECRET",
        "CHOREGOS_GITHUB_APP_PRIVATE_KEY",
        "CHOREGOS_EGRESS_REPORT_KEY",
        "CHOREGOS_TEMPORAL_ADDRESS",
        # `global.extraEnv` carries connector secrets: the API has it, the door does not.
        "CONNECTOR_TRACKER_TOKEN",
    ):
        assert never in api, f"{never}: the API no longer receives it, so the test proves nothing"
        assert never not in door, f"the door's Deployment receives {never}"


def test_dedicated_the_door_takes_its_own_extra_variables() -> None:
    wrapper = '{"name":"HOUSE_KMS_KEY","valueFrom":{"secretKeyRef":{"name":"kms","key":"id"}}}'
    documents = _documents(ON, HOSTS, DEDICATED, f"global.outposts.door.extraEnv=[{wrapper}]")
    assert _env(documents, f"Deployment/{DOOR}")["HOUSE_KMS_KEY"]["valueFrom"] == {
        "secretKeyRef": {"name": "kms", "key": "id"}
    }
    assert "HOUSE_KMS_KEY" not in _env(documents, "Deployment/choregos-api")
    shadow = 'global.outposts.door.extraEnv=[{"name":"CHOREGOS_DB_POOL_SIZE","value":"50"}]'
    assert "CHOREGOS_DB_POOL_SIZE" in _refusal(ON, HOSTS, DEDICATED, shadow)
    neither = 'global.outposts.door.extraEnv=[{"name":"HOUSE_KMS_KEY"}]'
    assert "exactly one of value or valueFrom" in _refusal(ON, HOSTS, DEDICATED, neither)


def test_dedicated_what_the_chart_gives_the_door_builds_the_door(monkeypatch: pytest.MonkeyPatch) -> None:
    """The entrypoint refuses to be built without outposts on, an origin and a key: the application of
    `python -m choregos_api.outposts`, built from the variables of the rendered Deployment, serves
    the four verbs and its two probes."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.outposts.alone import create_app
    from fastapi.routing import iter_route_contexts

    given = _settings_of(monkeypatch, _documents(ON, HOSTS, DEDICATED), f"Deployment/{DOOR}")
    assert given["CHOREGOS_OUTPOSTS"] == "on" and "PRIVATE KEY" in given["CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY"]
    try:
        served = {
            (method, context.path)
            for context in iter_route_contexts(create_app().router.routes)
            for method in (context.methods or ())
        }
    finally:
        monkeypatch.undo()
        reset_settings_cache()
    assert {path for _, path in served} == {
        "/outposts/v1/enrol",
        "/outposts/v1/poll",
        "/outposts/v1/calls/{id}/answer",
        "/outposts/v1/calls/{id}/refuse",
        "/healthz",
        "/readyz",
    }


def test_dedicated_a_pod_told_to_stop_is_given_longer_than_its_polls() -> None:
    """The process lets the polls it holds end by themselves, `GRACE_S` at most, then cancels what is
    left: the pod's own grace period is above that, or the kubelet would cut them first."""
    from choregos_api.outposts.alone import GRACE_S

    door = _named(_documents(ON, HOSTS, DEDICATED), "Deployment", DOOR)
    assert door is not None
    assert door["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] > GRACE_S == 30
    refusal = _refusal(ON, HOSTS, DEDICATED, f"global.outposts.door.terminationGracePeriodSeconds={GRACE_S}")
    assert "global.outposts.door.terminationGracePeriodSeconds" in refusal
    longer = _named(
        _documents(ON, HOSTS, DEDICATED, "global.outposts.door.terminationGracePeriodSeconds=90"),
        "Deployment",
        DOOR,
    )
    assert longer is not None and longer["spec"]["template"]["spec"]["terminationGracePeriodSeconds"] == 90


def test_dedicated_a_single_replica_has_no_disruption_budget() -> None:
    documents = _documents(ON, HOSTS, DEDICATED, "global.outposts.door.replicas=1")
    assert _named(documents, "PodDisruptionBudget", DOOR) is None


# ───────────────────────── the network: an entrance, and no way out to an outpost ─────────────────────────


def _policies(documents: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {d["metadata"]["name"]: d["spec"] for d in documents if d["kind"] == "NetworkPolicy"}


def test_outposts_open_no_way_out_of_the_platforms_namespace() -> None:
    """An outpost dials out: nothing here needs a route to one, and nothing gains one. Served by the
    API, the door changes no policy at all; split out, it adds one policy, its own."""
    before = _policies(_documents())
    assert before, "the chart renders its network policies by default"
    assert _policies(_documents(ON, HOSTS)) == before
    split = _policies(_documents(ON, HOSTS, DEDICATED))
    assert set(split) - set(before) == {"choregos-outposts-door-net"}
    for name, spec in before.items():
        if name != "choregos-allow-platform":
            assert split[name] == spec, f"{name} changed when the door was split out"
        assert split[name].get("egress") == spec.get("egress"), f"{name} gained a way out"


def test_dedicated_the_door_reaches_the_database_and_nothing_else() -> None:
    policies = _policies(_documents(ON, HOSTS, DEDICATED))
    door = policies["choregos-outposts-door-net"]
    assert door["podSelector"] == {"matchLabels": {"app.kubernetes.io/name": DOOR}}
    assert door["policyTypes"] == ["Ingress", "Egress"]
    dns, database = door["egress"]
    assert {port["port"] for port in dns["ports"]} == {53}
    assert database == {
        "to": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "choregos-data"}}}],
        "ports": [{"protocol": "TCP", "port": 5432}],
    }
    # Requests come from the entrance, not from the rest of the namespace.
    assert door["ingress"] == [
        {
            "from": [{"namespaceSelector": {"matchLabels": {"kubernetes.io/metadata.name": "gateway"}}}],
            "ports": [{"protocol": "TCP", "port": 8000}],
        }
    ]
    # Its pods are out of the platform's own egress policy (Temporal, the model gateway, the memory,
    # the plugin servers) and do not carry the label that admits every pod of the namespace.
    (excluded,) = policies["choregos-allow-platform"]["podSelector"]["matchExpressions"]
    assert excluded["operator"] == "NotIn" and DOOR in excluded["values"]
    deployment = _named(_documents(ON, HOSTS, DEDICATED), "Deployment", DOOR)
    assert deployment is not None
    labels = deployment["spec"]["template"]["metadata"]["labels"]
    assert "app.kubernetes.io/part-of" not in labels
    assert policies["choregos-allow-ingress"]["podSelector"] == {
        "matchLabels": {"app.kubernetes.io/part-of": "choregos"}
    }


def test_dedicated_with_an_embedded_database_the_door_reaches_that_one() -> None:
    policies = _policies(_documents(ON, HOSTS, DEDICATED, "global.database.embedded=true"))
    _, database = policies["choregos-outposts-door-net"]["egress"]
    assert database["to"] == [
        {"podSelector": {"matchLabels": {"app.kubernetes.io/name": "choregos-postgresql"}}}
    ]


# ───────────────────────── the alert on a second concurrent poll ─────────────────────────


@pytest.mark.parametrize(
    ("flavour", "kind"), [("prometheus", "PrometheusRule"), ("victoriametrics", "VMRule")]
)
def test_the_alert_on_a_second_poll_is_rendered_with_the_platforms_rules(flavour: str, kind: str) -> None:
    """ADR 0148 §7: two holders of one key is what a stolen key looks like. The rule reads the series
    the API exposes, in the chart's one group, in both flavours; off, it is not there."""
    from choregos_api.metrics import NAMES

    def rules(*sets: str) -> list[dict[str, Any]]:
        (rule,) = [d for d in _documents(f'monitoring.flavour="{flavour}"', *sets) if d["kind"] == kind]
        (group,) = rule["spec"]["groups"]
        assert group["name"] == "choregos.platform"
        return list(group["rules"])

    without = rules()
    assert "ChoregosOutpostPollConflict" not in {rule["alert"] for rule in without}
    (conflict,) = [rule for rule in rules(ON, HOSTS) if rule["alert"] == "ChoregosOutpostPollConflict"]
    assert "choregos_outpost_poll_conflicts_total" in NAMES
    assert "choregos_outpost_poll_conflicts_total" in conflict["expr"]
    assert conflict["labels"]["severity"] == "critical"
    # The organisation is named by the series' label, left for the alert manager to fill in.
    assert "{{ $labels.org }}" in conflict["annotations"]["description"]
    # The platform's own rules are the same, in the same order, before it.
    assert rules(ON, HOSTS)[: len(without)] == without


def test_no_alert_rule_where_the_chart_renders_none() -> None:
    documents = _documents(ON, HOSTS, "monitoring.alerts=false")
    assert not [d for d in documents if d["kind"] in {"PrometheusRule", "VMRule"}]
