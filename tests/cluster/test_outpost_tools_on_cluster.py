"""A tool call through an outpost on a cluster whose network plugin enforces policies (S65-19).

What `tests/e2e/test_outpost_tools.py` cannot show: that the two images start and find each other
over a network, that the outpost's chart installs a daemon that enrols and serves, and above all that
the platform cannot open a connection towards an outpost (ADR 0147 §1). The outpost's chart renders a
NetworkPolicy with no ingress rule; where a network plugin enforces it (kind with Calico), a
connection opened from the platform's namespace towards the outpost's is dropped and times out,
while the read the outpost dials out for goes through. A bench whose plugin ignores policies would
answer that connection "refused", or serve it: that is the difference this test reads.

- **The platform**, in a namespace of its own: PostgreSQL and the API image (`choregos-api:dev`),
  outposts switched on, the door at the in-cluster Service's origin, its signing key, the key
  wrapper and the run tokens' key derived from labels;
- **the outpost**, in another: the daemon image (`choregos-outpost:dev`) installed with
  `charts/choregos-outpost` and its NetworkPolicy, and a test MCP server beside it behind the alias
  (`assets/outpost_mcp_server.py`), which a NetworkPolicy of the test opens to the daemon's pods
  only, as the chart's own `servers` are.

The people's gestures go through the API (a port-forward of its Service, a development login): the
outpost is created, a token issued, the instance approved against the fingerprint its init container
printed, the connector declared, discovered and opened. The rows a run needs are written by the API's
own code, in its pod.

Skipped without a cluster (`conftest.context`), without `helm`, and without the two images:

    make images
    kind load docker-image choregos-api:dev choregos-outpost:dev --name choregos
    CHOREGOS_CLUSTER_CONTEXT=kind-choregos uv run pytest tests/cluster/test_outpost_tools_on_cluster.py
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from .conftest import context, kubectl, run_probe

pytestmark = [pytest.mark.cluster, pytest.mark.slow]

#: The two images, as `make images` tags them; another tag can be named for a bench of one's own.
API_IMAGE = os.environ.get("CHOREGOS_CLUSTER_API_IMAGE", "choregos-api:dev")
OUTPOST_IMAGE = os.environ.get("CHOREGOS_CLUSTER_OUTPOST_IMAGE", "choregos-outpost:dev")
CHART = Path(__file__).parents[2] / "charts" / "choregos-outpost"
SERVER_SCRIPT = Path(__file__).parent / "assets" / "outpost_mcp_server.py"
RELEASE = "site"
SLUG = "factory-floor"
ALIAS = "tracker"
CONNECTOR = "tracker"
RUN = "run-outpost-cluster"
ORG = "/api/v1/orgs/varga"
OUTPOST = f"{ORG}/outposts/{SLUG}"
SEARCH = f"/api/v1/internal/runs/{RUN}/tools/{CONNECTOR}__search_issues"
#: What selects the daemon's pods (the chart's `selectorLabels`).
DAEMON = {
    "app.kubernetes.io/name": "choregos-outpost",
    "app.kubernetes.io/instance": RELEASE,
    "app.kubernetes.io/component": "daemon",
}
#: curl's exit code when its time is up: the connection was neither accepted nor refused.
TIMED_OUT = 28
#: How long a step that waits for the cluster may take: the first start pulls images.
SLOW_S = 300.0


def _seed(label: str) -> bytes:
    """32 bytes derived from a label: no key of this test is written in the repository."""
    return hashlib.sha256(f"choregos cluster, outposts: {label}".encode()).digest()


def _ed25519_pem(label: str) -> str:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    private = Ed25519PrivateKey.from_private_bytes(_seed(label))
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return pem.decode()


def _p256_pem(label: str) -> str:
    """The run tokens' key, shared by the API's process and the script run in its pod."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    order = int("FFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551", 16)
    private = ec.derive_private_key(int.from_bytes(_seed(label), "big") % (order - 1) + 1, ec.SECP256R1())
    pem = private.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return pem.decode()


#: What opens the server behind the alias: in a Secret of the outpost's namespace, and nowhere else.
CREDENTIAL = _seed("the credential of the server behind the alias").hex()


def _image_present(image: str) -> bool:
    probe = subprocess.run(["docker", "image", "inspect", image], capture_output=True, text=True, check=False)
    return probe.returncode == 0


def _until[Value](read: Callable[[], Value | None], what: str, timeout_s: float = SLOW_S) -> Value:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        value = read()
        if value:
            return value
        time.sleep(3)
    raise AssertionError(f"not within {timeout_s:.0f} seconds: {what}")


# ───────────────────────── the platform's namespace ─────────────────────────


def _database(namespace: str) -> str:
    postgres = _deployment(
        namespace,
        "postgres",
        "postgres:16-alpine",
        port=5432,
        run_as=999,
        env=[
            {"name": "POSTGRES_USER", "value": "choregos"},
            {"name": "POSTGRES_PASSWORD", "value": "choregos"},
            {"name": "POSTGRES_DB", "value": "choregos"},
            {"name": "PGDATA", "value": "/tmp/pgdata"},
        ],
        ready={"exec": {"command": ["pg_isready", "-U", "choregos", "-h", "127.0.0.1"]}},
    )
    return yaml.safe_dump_all([postgres, _service(namespace, "postgres", 5432)])


def _platform(namespace: str) -> str:
    """The API, outposts on, the door at the Service's in-cluster origin. Applied once its database
    answers: its migrations run first, and a pod that is ready serves."""
    database = "postgresql+asyncpg://choregos:choregos@postgres:5432/choregos"
    keks = base64.b64encode(_seed("the key wrapper")).decode()
    env = [
        {"name": "CHOREGOS_FAKES", "value": "1"},
        {"name": "CHOREGOS_ENV", "value": "test"},
        {"name": "CHOREGOS_DEV_LOGIN_ENABLED", "value": "true"},
        {"name": "CHOREGOS_DEV_ADMIN_EMAILS", "value": "admin@varga.dev"},
        {"name": "CHOREGOS_DATABASE_URL", "value": database},
        {"name": "CHOREGOS_SESSION_SECRET", "value": _seed("the session secret").hex()},
        {"name": "CHOREGOS_RECONCILE_INTERVAL_SECONDS", "value": "0"},
        {"name": "CHOREGOS_RUN_TOKEN_PRIVATE_KEY", "value": _p256_pem("the run tokens")},
        {"name": "CHOREGOS_SECRETS_KEKS", "value": f"k1:{keks}"},
        {"name": "CHOREGOS_OUTPOSTS", "value": "on"},
        {"name": "CHOREGOS_OUTPOSTS_DOOR_URL", "value": f"http://api.{namespace}.svc:8000"},
        {"name": "CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY", "value": _ed25519_pem("the door's signing key")},
    ]
    api = _deployment(
        namespace,
        "api",
        API_IMAGE,
        port=8000,
        env=env,
        command=[
            "sh",
            "-c",
            "python -m choregos_api.migrate && uvicorn choregos_api.main:app --host 0.0.0.0 --port 8000",
        ],
        ready={"httpGet": {"path": "/healthz", "port": 8000}},
    )
    return yaml.safe_dump_all([api, _service(namespace, "api", 8000)])


def _deployment(
    namespace: str,
    name: str,
    image: str,
    *,
    port: int,
    env: list[dict[str, Any]],
    command: list[str] | None = None,
    run_as: int = 1000,
    volumes: list[dict[str, Any]] | None = None,
    mounts: list[dict[str, Any]] | None = None,
    ready: dict[str, Any] | None = None,
) -> dict[str, Any]:
    container: dict[str, Any] = {
        "name": name,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "env": env,
        "ports": [{"containerPort": port}],
        "securityContext": {"allowPrivilegeEscalation": False, "capabilities": {"drop": ["ALL"]}},
    }
    if command:
        container["command"] = command
    if mounts:
        container["volumeMounts"] = mounts
    if ready:
        container["readinessProbe"] = {**ready, "periodSeconds": 3}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": run_as,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "containers": [container],
                    "volumes": volumes or [],
                },
            },
        },
    }


def _service(namespace: str, name: str, port: int) -> dict[str, Any]:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": namespace},
        "spec": {"selector": {"app": name}, "ports": [{"port": port, "targetPort": port}]},
    }


def _in_the_api(namespace: str, script: str) -> dict[str, Any]:
    """Runs a script with the API's own code and settings, in its pod; returns the JSON it printed."""
    out = kubectl("-n", namespace, "exec", "deploy/api", "--", "python", "-c", script, timeout_s=SLOW_S)
    return dict(json.loads(out.strip().splitlines()[-1]))


SEED_ORGANISATION = """
import asyncio, json
from choregos_api.db.models import Organization, Project
from choregos_api.db.session import session_scope
from choregos_api.services import ensure_defaults

async def main():
    async with session_scope(orgs="*") as session:
        org = Organization(slug="varga", name="Varga Foundation")
        session.add(org)
        await session.flush()
        repo = {"url": "https://github.com/varga/billing-api.git", "default_branch": "main"}
        config = {"slug": "billing-api", "org": "varga", "repo": repo}
        project = Project(
            org_id=org.id, slug="billing-api", name="Billing API", status="active", config=config
        )
        session.add(project)
        await session.flush()
        await ensure_defaults(session, project)
        print(json.dumps({"project": project.id}))

asyncio.run(main())
"""

SEED_RUN = """
import asyncio, json
from choregos_api.db.models import Run, WorkItem
from choregos_api.db.session import session_scope
from choregos_api.security import mint_run_token

async def main():
    async with session_scope(orgs="*") as session:
        item = WorkItem(project_id="{project}", tracker_key="varga/billing-api#7", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(Run(id="{run}", work_item_id=item.id, project_id="{project}", stage_role="plan",
                        transition_id="t-plan", status="running", agent_slug="coordinator", agent_version=1,
                        initiated_by="person:admin@varga.dev"))
    token = mint_run_token("{run}", project_slug="billing-api", work_item_key="varga/billing-api#7",
                           ttl_minutes=30)
    print(json.dumps({{"token": token}}))

asyncio.run(main())
"""


def _port_forward(namespace: str) -> tuple[subprocess.Popen[str], int]:
    """The API's Service on a port of this machine, for the people's gestures."""
    forward = subprocess.Popen(
        ["kubectl", "--context", context(), "-n", namespace, "port-forward", "svc/api", ":8000"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert forward.stdout is not None
    # "Forwarding from 127.0.0.1:41373 -> 8000"
    line = forward.stdout.readline()
    found = re.search(r"127\.0\.0\.1:(\d+)", line)
    assert found, f"no port-forward: {line!r}"
    return forward, int(found.group(1))


# ───────────────────────── the outpost's namespace ─────────────────────────


def _server_beside_the_outpost(namespace: str) -> str:
    """The test MCP server behind the alias, its credential, and a NetworkPolicy that admits the
    daemon's pods and nobody else, as the chart's own `servers` do."""
    secret = {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {"name": "tracker-mcp-credential", "namespace": namespace},
        "stringData": {"credential": CREDENTIAL},
    }
    script = {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": "tracker-mcp", "namespace": namespace},
        "data": {"server.py": SERVER_SCRIPT.read_text(encoding="utf-8")},
    }
    credential = {"secretKeyRef": {"name": "tracker-mcp-credential", "key": "credential"}}
    server = _deployment(
        namespace,
        "tracker-mcp",
        OUTPOST_IMAGE,
        port=8080,
        env=[
            {"name": "EXPECTED_CREDENTIAL", "valueFrom": credential},
            {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
        ],
        command=["python", "/srv/mcp/server.py"],
        volumes=[{"name": "script", "configMap": {"name": "tracker-mcp"}}],
        mounts=[{"name": "script", "mountPath": "/srv/mcp", "readOnly": True}],
        ready={"httpGet": {"path": "/healthz", "port": 8080}},
    )
    admitted = {
        "apiVersion": "networking.k8s.io/v1",
        "kind": "NetworkPolicy",
        "metadata": {"name": "tracker-mcp", "namespace": namespace},
        "spec": {
            "podSelector": {"matchLabels": {"app": "tracker-mcp"}},
            "policyTypes": ["Ingress"],
            "ingress": [
                {
                    "from": [{"podSelector": {"matchLabels": DAEMON}}],
                    "ports": [{"protocol": "TCP", "port": 8080}],
                }
            ],
        },
    }
    return yaml.safe_dump_all([secret, script, server, _service(namespace, "tracker-mcp", 8080), admitted])


def _values(namespace: str, platform: str, given: dict[str, Any]) -> dict[str, Any]:
    """The chart's values, as the outpost's operator writes them from what the console gave."""
    # An image loaded into kind by its short name is `docker.io/library/<name>` on the nodes.
    name, tag = OUTPOST_IMAGE.rsplit(":", 1)
    registry, _, repository = name.rpartition("/")
    return {
        "image": {
            "registry": registry or "docker.io/library",
            "repository": repository,
            "tag": tag,
            "pullPolicy": "IfNotPresent",
        },
        "enrolment": {"existingSecret": f"{RELEASE}-enrolment", "key": "token"},
        "graceSeconds": 5,
        "terminationGracePeriodSeconds": 10,
        "policy": {
            "control_plane": given["control_plane"],
            "door_keys": given["door_keys"],
            "endpoints": {
                ALIAS: {
                    "url": f"http://tracker-mcp.{namespace}.svc:8080/mcp",
                    "tools": ["search_*"],
                    "auth": {
                        "header": "Authorization",
                        "secretName": "tracker-mcp-credential",
                        "secretKey": "credential",
                    },
                }
            },
        },
        # The door is a Service of this cluster: the daemon's way to it is the platform's API pods.
        "networkPolicy": {
            "enabled": True,
            "controlPlane": {"namespace": platform, "podLabels": {"app": "api"}, "port": 8000},
        },
    }


def _helm(*args: str) -> str:
    command = ["helm", "--kube-context", context(), *args]
    done = subprocess.run(command, capture_output=True, text=True, timeout=SLOW_S, check=False)
    assert done.returncode == 0, f"{' '.join(command)} → {done.returncode}\n{done.stderr[:800]}"
    return done.stdout


def _daemon_pod(namespace: str) -> str:
    selector = ",".join(f"{name}={value}" for name, value in DAEMON.items())
    return _until(
        lambda: kubectl(
            "-n",
            namespace,
            "get",
            "pod",
            "-l",
            selector,
            "-o",
            "jsonpath={.items[0].metadata.name}",
            check=False,
        ).strip(),
        "the daemon's pod exists",
    )


def _printed_fingerprint(namespace: str, pod: str) -> str:
    """The fingerprint the enrolment printed, read where its operator reads it."""

    def printed() -> str | None:
        logs = kubectl("-n", namespace, "logs", pod, "-c", "enrol", check=False)
        found = re.search(r"SHA256:[A-Za-z0-9+/=_-]{43}", logs)
        return found.group(0) if found else None

    return _until(printed, "the init container enrolled the instance and printed its fingerprint")


# ───────────────────────── the bench ─────────────────────────


def _online(http: httpx.Client) -> bool:
    shown = http.get(OUTPOST).json()
    return bool(shown["online"]) and shown["endpoints"] == [ALIAS]


@dataclass
class Site:
    platform: str
    outpost: str
    http: httpx.Client
    headers: dict[str, str]
    pod: str

    def search(self, query: str) -> dict[str, Any]:
        answer = self.http.post(SEARCH, headers=self.headers, json={"query": query})
        assert answer.status_code == 200, answer.text
        return dict(answer.json())


@pytest.fixture(scope="module")
def site() -> Iterator[Site]:
    context()
    for image in (API_IMAGE, OUTPOST_IMAGE):
        if not _image_present(image):
            pytest.skip(f"image `{image}` is absent: `make images`, then `kind load docker-image {image}`")
    if shutil.which("helm") is None:
        pytest.skip("helm is absent")
    suffix = secrets.token_hex(3)
    platform, outpost = f"choregos-op-platform-{suffix}", f"choregos-op-site-{suffix}"
    forward: subprocess.Popen[str] | None = None
    http: httpx.Client | None = None
    try:
        for namespace in (platform, outpost):
            kubectl("create", "namespace", namespace)
        kubectl("apply", "-f", "-", input_text=_database(platform))
        kubectl("-n", platform, "rollout", "status", "deploy/postgres", "--timeout=300s", timeout_s=400)
        kubectl("apply", "-f", "-", input_text=_platform(platform))
        kubectl("-n", platform, "rollout", "status", "deploy/api", "--timeout=300s", timeout_s=400)
        project_id = _in_the_api(platform, SEED_ORGANISATION)["project"]
        forward, port = _port_forward(platform)
        http = httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=60.0)
        login = http.get("/api/v1/auth/login", params={"as": "admin@varga.dev"})
        http.get(login.headers["location"])

        # A person creates the outpost and issues a token for one instance.
        assert http.post(f"{ORG}/outposts", json={"slug": SLUG}).status_code == 201
        issued = http.post(f"{OUTPOST}/enrolments")
        assert issued.status_code == 201, issued.text
        given = issued.json()
        assert given["control_plane"] == f"http://api.{platform}.svc:8000"

        # The operator installs the outpost in their namespace, the token in a Secret of their own.
        kubectl(
            "-n",
            outpost,
            "create",
            "secret",
            "generic",
            f"{RELEASE}-enrolment",
            f"--from-literal=token={given['token']}",
        )
        kubectl("apply", "-f", "-", input_text=_server_beside_the_outpost(outpost))
        kubectl("-n", outpost, "rollout", "status", "deploy/tracker-mcp", "--timeout=300s", timeout_s=400)
        with tempfile.TemporaryDirectory() as scratch:
            values = Path(scratch) / "values.yaml"
            values.write_text(yaml.safe_dump(_values(outpost, platform, given)), encoding="utf-8")
            _helm("install", RELEASE, str(CHART), "--namespace", outpost, "--values", str(values))
        pod = _daemon_pod(outpost)
        printed = _printed_fingerprint(outpost, pod)

        # A person approves the instance against the fingerprint the operator read in the logs.
        (instance,) = http.get(f"{OUTPOST}/instances").json()["items"]
        assert instance["fingerprint"] == printed
        approved = http.post(f"{OUTPOST}/instances/{instance['id']}/approve", json={"fingerprint": printed})
        assert approved.status_code == 200, approved.text
        _until(lambda: _online(http), "the outpost is online for its alias")

        # The connector, its tools discovered through the outpost, the read opened, a run.
        config = {"outpost": SLUG, "endpoint": ALIAS, "timeout_s": 60}
        declared = http.post(
            f"{ORG}/connectors", json={"name": CONNECTOR, "type": "mcp-outpost", "config": config}
        )
        assert declared.status_code == 201, declared.text
        discovered = http.post(f"{ORG}/connectors/{CONNECTOR}/discover")
        assert discovered.status_code == 200, discovered.text
        operations = http.get(f"{ORG}/connectors/{CONNECTOR}").json()["operations"]
        assert [(op["name"], op["policy"]) for op in operations] == [("search_issues", "forbidden")]
        opened = http.patch(
            f"{ORG}/connectors/{CONNECTOR}/operations/search_issues", json={"policy": "allowed"}
        )
        assert opened.status_code == 200, opened.text
        spec = {"mcp_servers": [{"connector": CONNECTOR, "tools": ["*"]}]}
        agent = http.post(
            f"{ORG}/agents", json={"slug": "coordinator", "display_name": "Coordinator", "spec": spec}
        )
        assert agent.status_code == 201, agent.text
        token = _in_the_api(platform, SEED_RUN.format(project=project_id, run=RUN))["token"]
        yield Site(platform, outpost, http, {"Authorization": f"Bearer {token}"}, pod)
    finally:
        if http is not None:
            http.close()
        if forward is not None:
            forward.terminate()
            forward.wait(timeout=10)
        subprocess.run(
            ["helm", "--kube-context", context(), "uninstall", RELEASE, "--namespace", outpost],
            capture_output=True,
            timeout=SLOW_S,
            check=False,
        )
        kubectl("delete", "namespace", platform, outpost, "--wait=false", "--ignore-not-found", check=False)


def test_the_read_goes_through_while_the_platform_cannot_connect_to_the_outpost(site: Site) -> None:
    from choregos_contracts import arguments_sha256

    first = site.search("door stuck")
    assert first["status_code"] == 200, first
    assert "answered by the server behind the alias" in first["result"]["content"][0]["text"]

    # From the platform's namespace, towards the outpost's: the daemon's pod (it listens on nothing,
    # so a connection that reached it would be refused at once) and the server behind the alias.
    address = kubectl("-n", site.outpost, "get", "pod", site.pod, "-o", "jsonpath={.status.podIP}").strip()
    assert address, "the daemon's pod has an address"
    probe = run_probe(
        site.platform,
        "probe-towards-the-outpost",
        f"curl -s -m 5 -o /dev/null http://{address}:8080/; echo daemon=$?; "
        f"curl -s -m 5 -o /dev/null http://tracker-mcp.{site.outpost}.svc:8080/healthz; echo server=$?; "
        "curl -s -m 20 -o /dev/null -w 'api=%{http_code}' http://api:8000/healthz; echo \" exit=$?\"",
    )
    assert f"daemon={TIMED_OUT}" in probe, f"a connection to the daemon's pod was not dropped: {probe}"
    assert f"server={TIMED_OUT}" in probe, (
        f"a connection to the server behind the alias was not dropped: {probe}"
    )
    assert "api=200 exit=0" in probe, f"the probe itself could not connect where nothing forbids it: {probe}"

    # And the outpost, which dials out, still serves.
    second = site.search("still stuck")
    assert second["status_code"] == 200, second

    listed = site.http.get(f"{OUTPOST}/calls").json()["items"]
    calls = {item["id"]: item for item in listed if item["kind"] == "call_tool"}
    assert {item["state"] for item in calls.values()} == {"answered"} and len(calls) == 2
    # The outpost's own audit log, read in its pod, names the same calls with the same digests.
    audit = kubectl(
        "-n",
        site.outpost,
        "exec",
        site.pod,
        "-c",
        "outpost",
        "--",
        "cat",
        "/var/log/choregos-outpost/audit.jsonl",
    )
    executed = {
        line["call_id"]: line
        for line in map(json.loads, audit.splitlines())
        if line["event"] == "executed" and line.get("kind") == "call_tool"
    }
    for call_id, item in calls.items():
        assert executed[call_id]["arguments_sha256"] == item["arguments_sha256"]
    assert {line["arguments_sha256"] for line in executed.values()} >= {
        arguments_sha256({"query": "door stuck"}),
        arguments_sha256({"query": "still stuck"}),
    }
    # The server was opened with the outpost's credential, every time.
    served = [
        json.loads(line) for line in kubectl("-n", site.outpost, "logs", "deploy/tracker-mcp").splitlines()
    ]
    assert served and all(line["authorized"] for line in served)
    assert [line["tool"] for line in served if line["method"] == "tools/call"] == ["search_issues"] * 2
