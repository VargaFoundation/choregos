"""The local install (`compose.yaml` at the root, S28-01): what `docker compose up` must keep true.

Running it is the job of the CI job that starts it (S28-04); these checks read the file, so that
a change that would break a laptop install fails here first, in a second.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
COMPOSE = ROOT / "compose.yaml"


def _services() -> dict[str, Any]:
    return dict(yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"])


def _env(service: dict[str, Any]) -> dict[str, str]:
    return {k: str(v) for k, v in (service.get("environment") or {}).items()}


def test_the_platform_and_its_dependencies_are_all_there() -> None:
    expected = {"postgres", "temporal", "litellm", "keys", "migrate", "api", "worker", "seed", "web"}
    assert expected <= set(_services())


def test_no_platform_process_runs_with_global_fakes() -> None:
    """`CHOREGOS_FAKES=1` swaps Temporal for an in-memory fake in the API (`temporal.py`): the
    compose would start a Temporal server that nothing talks to, and no work item would run.
    Fakes mode is per project here (the demo project's connectors)."""
    for name in ("api", "worker", "seed", "migrate"):
        assert "CHOREGOS_FAKES" not in _env(_services()[name]), name


def test_development_sign_in_only_comes_with_the_dev_environment() -> None:
    """The API refuses development sign-in in staging and prod: the compose must say `dev`."""
    for name in ("api", "worker", "seed", "migrate"):
        env = _env(_services()[name])
        assert env["CHOREGOS_ENV"] == "dev", name


def test_the_platform_connects_as_the_non_superuser_role() -> None:
    """A superuser ignores row-level security: the API must connect as `choregos_app`."""
    url = _env(_services()["api"])["CHOREGOS_DATABASE_URL"]
    assert "://choregos_app:" in url
    assert "choregos_app" in (ROOT / "dev/local/initdb/01-roles.sql").read_text(encoding="utf-8")


def test_published_ports_listen_on_localhost_only() -> None:
    for name, service in _services().items():
        for port in service.get("ports", []):
            assert str(port).startswith("127.0.0.1:"), f"{name} publishes {port} on every interface"


def test_the_console_is_on_3000_by_default() -> None:
    assert "127.0.0.1:${CHOREGOS_PORT_WEB:-3000}:3000" in _services()["web"]["ports"]


def test_every_image_is_built_here_or_pinned() -> None:
    for name, service in _services().items():
        if "build" in service:
            dockerfile = ROOT / service["build"]["dockerfile"]
            assert dockerfile.exists(), f"{name}: {dockerfile} is missing"
            continue
        image = str(service["image"])
        assert ":" in image.rsplit("/", 1)[-1] and not image.endswith(":latest"), f"{name}: {image}"


def test_the_api_and_the_workers_share_the_run_token_key() -> None:
    """The workers sign run tokens, the API verifies them: two keys and every agent call is refused."""
    services = _services()
    for name in ("api", "worker"):
        assert "run-token-key:/keys:ro" in services[name]["volumes"], name
        assert "/keys/run-token.pem" in " ".join(services[name]["command"]), name


def test_the_console_is_built_with_development_sign_in_and_the_api_address() -> None:
    args = _services()["web"]["build"]["args"]
    assert args["NEXT_PUBLIC_DEV_LOGIN"] == "true"
    assert args["CHOREGOS_API_URL"] == "http://api:8000"
    dockerfile = (ROOT / "docker/web.Dockerfile").read_text(encoding="utf-8")
    assert "ARG CHOREGOS_API_URL=" in dockerfile and "ARG NEXT_PUBLIC_DEV_LOGIN=" in dockerfile


def test_the_build_context_leaves_local_artefacts_and_secrets_out() -> None:
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8").split()
    for entry in ("**/node_modules", "**/.venv", "**/.env", ".git"):
        assert entry in ignored, entry


def test_the_memory_service_is_optional() -> None:
    assert _services()["ecphoria"]["profiles"] == ["memory"]


def test_the_dependencies_only_stack_is_another_compose_project() -> None:
    """Same project name: each file would see the other's containers as orphans, and share volumes."""
    deps = yaml.safe_load((ROOT / "dev/compose.yaml").read_text(encoding="utf-8"))["name"]
    local = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["name"]
    assert deps != local


# ───────────────────────── S28-02: a real model, the local Docker executor ─────────────────────────


def test_only_litellm_and_its_configuration_see_the_provider_key() -> None:
    """Agents get a virtual key per run (LiteLLM, minted by the platform), never the provider's."""
    for name, service in _services().items():
        holds = {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"} & set(_env(service))
        if name in {"litellm", "litellm-config"}:
            assert holds == {"ANTHROPIC_API_KEY", "OPENAI_API_KEY"}, name
        else:
            assert not holds, f"{name} receives {holds}"


def test_litellm_reads_the_generated_configuration() -> None:
    services = _services()
    assert "litellm-config:/litellm:ro" in services["litellm"]["volumes"]
    assert services["litellm"]["command"][:2] == ["--config", "/litellm/config.yaml"]
    condition = services["litellm"]["depends_on"]["litellm-config"]["condition"]
    assert condition == "service_completed_successfully"
    assert _env(services["seed"])["CHOREGOS_LOCAL_PROVIDER_FILE"] == "/litellm/provider"


def test_the_workers_run_agents_as_local_containers() -> None:
    from choregos_api.local_demo import LOCAL_NETWORK

    compose = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    worker = compose["services"]["worker"]
    assert worker["build"]["target"] == "worker-local"
    assert "/var/run/docker.sock:/var/run/docker.sock" in worker["volumes"]
    env = _env(worker)
    assert env["CHOREGOS_EXECUTOR_KIND"] == "local_docker"
    assert env["CHOREGOS_RUNNER_IMAGE"].startswith("ghcr.io/vargafoundation/choregos-runner:")
    # The run containers join the network by name: it must not depend on the project name.
    assert compose["networks"]["default"]["name"] == LOCAL_NETWORK


def test_the_worker_local_target_has_the_docker_cli_and_is_not_the_default_build() -> None:
    dockerfile = (ROOT / "docker/api.Dockerfile").read_text(encoding="utf-8")
    stages = [line.split()[-1] for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert "worker-local" in stages
    assert stages[-1] == "worker", "a build without --target must still produce the published worker"
    assert "/usr/local/bin/docker" in dockerfile.split("AS worker-local", 1)[1].split("FROM ", 1)[0]
