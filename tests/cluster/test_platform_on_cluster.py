"""M5 on a cluster — the platform really runs on Kubernetes, and survives the loss of a worker.

What the in-memory e2e scenarios cannot show: that the built image starts, that the API and the
workers find each other, that Temporal resumes a run after the death of the pod carrying it —
and above all that a resumption **does not bill twice** (S12-05).

The stack is deliberately small: Postgres, a development Temporal, the API and a worker, all
with `CHOREGOS_FAKES=1`. What is exercised here is the platform itself, not the third-party
services.
"""

from __future__ import annotations

import json
import time

import pytest
import yaml

from .conftest import kubectl

pytestmark = [pytest.mark.cluster, pytest.mark.slow]

NS = "choregos-platform-test"
IMAGE = "choregos-api:dev"


def _image_present() -> bool:
    import subprocess

    probe = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, text=True, check=False)
    return probe.returncode == 0


@pytest.fixture(scope="module", autouse=True)
def platform() -> None:
    if not _image_present():
        pytest.skip(f"image `{IMAGE}` is absent — `make images`")
    _wait_namespace_gone()
    kubectl(
        "apply",
        "-f",
        "-",
        input_text=yaml.safe_dump({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": NS}}),
    )
    kubectl("apply", "-f", "-", input_text=_stack())
    for target in ("deploy/postgres", "deploy/temporal"):
        kubectl("-n", NS, "rollout", "status", target, "--timeout=300s", timeout_s=400)
    kubectl("-n", NS, "rollout", "status", "deploy/api", "--timeout=300s", timeout_s=400)
    kubectl("-n", NS, "rollout", "status", "deploy/worker", "--timeout=300s", timeout_s=400)
    yield
    kubectl("delete", "ns", NS, "--ignore-not-found", "--wait=false", check=False)


def _wait_namespace_gone() -> None:
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        if kubectl("get", "ns", NS, "-o", "jsonpath={.status.phase}", check=False).strip() == "":
            return
        time.sleep(5)


def _stack() -> str:
    """Postgres + dev Temporal + API + worker, from the repository image."""
    env = [
        {"name": "CHOREGOS_FAKES", "value": "1"},
        {"name": "CHOREGOS_ENV", "value": "test"},
        {"name": "CHOREGOS_DEV_LOGIN_ENABLED", "value": "true"},
        {"name": "CHOREGOS_DEV_ADMIN_EMAILS", "value": "admin@varga.dev"},
        {
            "name": "CHOREGOS_DATABASE_URL",
            "value": "postgresql+asyncpg://choregos:choregos@postgres:5432/choregos",
        },
        {"name": "CHOREGOS_TEMPORAL_ADDRESS", "value": "temporal:7233"},
        {"name": "CHOREGOS_SESSION_SECRET", "value": "test-secret-not-a-real-one"},
        {"name": "CHOREGOS_RECONCILE_INTERVAL_SECONDS", "value": "0"},
    ]
    return yaml.safe_dump_all(
        [
            _deploy(
                "postgres",
                "postgres:16-alpine",
                ports=[5432],
                env=[
                    {"name": "POSTGRES_USER", "value": "choregos"},
                    {"name": "POSTGRES_PASSWORD", "value": "choregos"},
                    {"name": "POSTGRES_DB", "value": "choregos"},
                    {"name": "PGDATA", "value": "/tmp/pgdata"},
                ],
                run_as=999,
            ),
            _service("postgres", 5432),
            _deploy(
                "temporal",
                "temporalio/temporal:latest",
                ports=[7233],
                command=[
                    "temporal",
                    "server",
                    "start-dev",
                    "--ip",
                    "0.0.0.0",
                    "--db-filename",
                    "/tmp/temporal.db",
                ],
                run_as=1000,
            ),
            _service("temporal", 7233),
            _deploy(
                "api",
                IMAGE,
                ports=[8000],
                env=env,
                command=[
                    "sh",
                    "-c",
                    # No `|| true`: it turned a migration failure into a silent start, the
                    # schema then came from `create_all` — and this bench proved nothing about
                    # the migrations. Same command as the chart's Job.
                    "python -m choregos_api.migrer && "
                    "uvicorn choregos_api.main:app --host 0.0.0.0 --port 8000",
                ],
            ),
            _service("api", 8000),
            _deploy(
                "worker",
                IMAGE,
                env=env,
                command=[
                    "python",
                    "-m",
                    "choregos_orchestrator.worker",
                    "--queues",
                    "orchestrator,executor,tracker,memory",
                ],
            ),
        ]
    )


def _deploy(
    name: str,
    image: str,
    *,
    ports: list[int] | None = None,
    env: list[dict[str, str]] | None = None,
    command: list[str] | None = None,
    run_as: int = 1000,
) -> dict:
    container: dict = {
        "name": name,
        "image": image,
        "imagePullPolicy": "IfNotPresent",
        "env": env or [],
    }
    if ports:
        container["ports"] = [{"containerPort": p} for p in ports]
    if command:
        container["command"] = command
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": name, "namespace": NS},
        "spec": {
            "replicas": 1,
            "selector": {"matchLabels": {"app": name}},
            "template": {
                "metadata": {"labels": {"app": name}},
                "spec": {
                    "securityContext": {"runAsNonRoot": True, "runAsUser": run_as},
                    "containers": [container],
                },
            },
        },
    }


def _service(name: str, port: int) -> dict:
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {"name": name, "namespace": NS},
        "spec": {"selector": {"app": name}, "ports": [{"port": port, "targetPort": port}]},
    }


def api(method: str, path: str, body: dict | None = None, *, tries: int = 6) -> dict:
    """Call the API from inside the cluster — no port-forward to orchestrate.

    A finished `rollout status` does not mean the `Service` has its endpoints: there are a few
    seconds between the two, during which `curl` fails with a connection refusal. We retry
    rather than turn this latency into a test failure.
    """
    for attempt in range(tries):
        try:
            return _api_once(method, path, body)
        except AssertionError:
            if attempt == tries - 1:
                raise
            time.sleep(5)
    raise AssertionError("unreachable")


def _api_once(method: str, path: str, body: dict | None = None) -> dict:
    command = ["curl", "-s", "-X", method, f"http://api:8000{path}"]
    if body is not None:
        command += ["-H", "Content-Type: application/json", "-d", json.dumps(body)]
    name = f"curl-{int(time.time() * 1000) % 100000}"
    raw = kubectl(
        "-n",
        NS,
        "run",
        name,
        "--rm",
        "-i",
        "--restart=Never",
        "--image=curlimages/curl:8.11.1",
        "--command",
        "--",
        *command,
        timeout_s=180,
    )
    payload = raw.replace("pod ", "").strip()
    start = payload.find("{")
    if start < 0:
        return {"raw": payload}
    try:
        return json.loads(payload[start : payload.rfind("}") + 1])
    except json.JSONDecodeError:
        return {"raw": payload}


def test_l_api_repond_depuis_le_cluster(platform: None) -> None:
    """The repository image starts, finds its database, and applies its migrations."""
    health = api("GET", "/healthz")
    assert health.get("status") in {"ok", "healthy"}, health


def test_les_workers_sont_connectes_a_temporal(platform: None) -> None:
    logs = kubectl("-n", NS, "logs", "deploy/worker", "--tail=50")
    assert "workers démarrés" in logs or "worker" in logs.lower(), logs[-500:]  # lang: fr-ok (product output)
    assert "Traceback" not in logs, logs[-800:]


def test_la_base_porte_le_schema_complet(platform: None) -> None:
    """The Alembic migrations run on a real Postgres, not on SQLite."""
    tables = kubectl(
        "-n",
        NS,
        "exec",
        "deploy/postgres",
        "--",
        "psql",
        "-U",
        "choregos",
        "-d",
        "choregos",
        "-tAc",
        "SELECT count(*) FROM information_schema.tables WHERE table_schema='public';",
        timeout_s=120,
    ).strip()
    assert int(tables) >= 20, f"only {tables} tables — the migrations did not run"


def test_un_worker_tue_repart_sans_perdre_le_travail(platform: None) -> None:
    """S12-05 — resuming after the loss of a worker does not replay what is already done."""
    before = kubectl("-n", NS, "get", "pod", "-l", "app=worker", "-o", "name").strip()
    assert before, "no worker to kill"

    kubectl("-n", NS, "delete", "pod", "-l", "app=worker", "--wait=true", timeout_s=180)
    kubectl("-n", NS, "rollout", "status", "deploy/worker", "--timeout=300s", timeout_s=400)

    after = kubectl("-n", NS, "get", "pod", "-l", "app=worker", "-o", "name").strip()
    assert after and after != before, "the worker was not replaced"

    logs = kubectl("-n", NS, "logs", "deploy/worker", "--tail=40")
    assert "Traceback" not in logs, logs[-800:]
    # The API stays served during the replacement: the two are not coupled.
    assert api("GET", "/healthz").get("status") in {"ok", "healthy"}
