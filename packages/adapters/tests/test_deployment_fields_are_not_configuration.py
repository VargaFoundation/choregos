"""S65-03, ADR 0150: a connector's configuration never says what only the deployment says.

Which cluster the platform talks to (`kubernetes`), and what a run's pod is given from that cluster
(`env_from_secrets`, `image_pull_secrets`, `service_account`), were read from a `runtime` connector's
configuration, which any holder of `connector:write` on a project declares. They are refused when a
connector is declared, refused again when a stored one is built, and no longer read by the factories.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters import build, validate_config
from choregos_adapters.errors import ConfigurationError
from choregos_adapters.registry import DEPLOYMENT_FIELDS, AdapterSet, refuse_deployment_fields, spec_of

ELSEWHERE: dict[str, Any] = {"kubernetes": {"base_url": "https://k8s.elsewhere.example", "verify": False}}
KUBE: dict[str, Any] = {"kubernetes": {"base_url": "https://k8s.test", "token": "t", "verify": False}}

RUNTIME_SETTINGS: list[dict[str, Any]] = [
    ELSEWHERE,
    {"env_from_secrets": ["a-secret-of-the-namespace"]},
    {"image_pull_secrets": ["a-pull-secret"]},
    {"service_account": "another-account"},
]


@pytest.mark.parametrize("type_name", ["k8s_job", "agent_sandbox", "tekton"])
@pytest.mark.parametrize("config", RUNTIME_SETTINGS, ids=lambda config: next(iter(config)))
def test_a_declared_runtime_that_sets_what_the_deployment_sets_is_refused(
    type_name: str, config: dict[str, Any]
) -> None:
    with pytest.raises(ConfigurationError, match="the deployment's to set") as refused:
        validate_config("runtime", type_name, config)
    assert f"`{next(iter(config))}`" in str(refused.value)


def test_a_ci_connector_does_not_name_the_api_server_either() -> None:
    with pytest.raises(ConfigurationError, match="the deployment's to set"):
        validate_config("ci", "tekton", ELSEWHERE)
    # Where a project's pipelines run is the project's to say.
    validate_config("ci", "tekton", {"namespace": "proj-billing-ci", "pipeline": "billing-ci"})


def test_every_field_named_is_said_at_once() -> None:
    config = {"env_from_secrets": ["x"], "service_account": "y", "max_active": 2}
    with pytest.raises(ConfigurationError) as refused:
        refuse_deployment_fields("runtime", config)
    assert "`env_from_secrets`, `service_account`" in str(refused.value)
    assert "max_active" not in str(refused.value)


@pytest.mark.parametrize("blank", [None, "", [], {}])
def test_a_blank_value_sets_nothing_and_is_let_through(blank: Any) -> None:
    """A form that saved every field of a type, blank ones included, must not stop its project's runs."""
    for name in DEPLOYMENT_FIELDS["runtime"]:
        refuse_deployment_fields("runtime", {name: blank})


def test_what_a_project_tunes_is_not_refused() -> None:
    validate_config("runtime", "k8s_job", {"max_active": 2, "cpu_limit": "1", "memory_limit": "4Gi"})
    refuse_deployment_fields("runtime", None)


def test_another_kind_may_have_a_field_of_that_name() -> None:
    refuse_deployment_fields("gateway", {"kubernetes": {"base_url": "https://x.example"}})
    refuse_deployment_fields("ci", {"service_account": "x"})


def test_the_types_no_longer_declare_them() -> None:
    """What a type declares is what a form offers: a field that is refused is not offered."""
    for type_name in ("k8s_job", "agent_sandbox", "tekton"):
        spec = spec_of("runtime", type_name)
        assert spec is not None
        declared = set((spec.config_schema or {}).get("properties", {}))
        assert not declared & DEPLOYMENT_FIELDS["runtime"], (type_name, declared)


@pytest.mark.parametrize("config", RUNTIME_SETTINGS, ids=lambda config: next(iter(config)))
def test_a_stored_configuration_that_sets_them_is_not_built(
    monkeypatch: pytest.MonkeyPatch, config: dict[str, Any]
) -> None:
    """A row stored before the declaration refused it: refused before any adapter is built."""
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    with pytest.raises(ConfigurationError, match="the deployment's to set"):
        AdapterSet.from_connectors({"runtime": {"type": "k8s_job", "config": config}})


def test_the_code_that_builds_an_executor_may_still_name_the_api_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The seam the conformance suite and the cluster tests use: `build`, not a declaration."""
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    executor = build("runtime", "k8s_job", KUBE)
    assert executor.client.base_url == "https://k8s.test"
    assert executor.client.token == "t"


@pytest.mark.parametrize("type_name", ["k8s_job", "agent_sandbox"])
def test_the_factories_take_the_pod_s_secrets_from_the_deployment_alone(
    monkeypatch: pytest.MonkeyPatch, type_name: str
) -> None:
    """Even handed to `build`, a configuration does not choose them: the deployment's list is used."""
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    monkeypatch.setenv("CHOREGOS_RUNNER_ENV_SECRETS", "agent-credentials, ")
    monkeypatch.setenv("CHOREGOS_RUNNER_IMAGE_PULL_SECRETS", "registry-pull")
    asked = {**KUBE, "env_from_secrets": ["x"], "image_pull_secrets": ["y"], "service_account": "z"}
    executor = build("runtime", type_name, asked)
    assert executor.env_from_secrets == ["agent-credentials"]
    if type_name == "k8s_job":
        assert executor.image_pull_secrets == ["registry-pull"]
        assert executor.service_account == "choregos-runner"


def test_the_tekton_executor_runs_as_the_runner_s_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
    executor = build("runtime", "tekton", {**KUBE, "service_account": "z"})
    assert executor.service_account == "choregos-runner"
