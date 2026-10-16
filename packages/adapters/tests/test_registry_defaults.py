"""S40-05: a platform default is claimed only for what the deployment really provides."""

from __future__ import annotations

import pytest
from choregos_adapters import deployment_default_type


def test_the_runtime_is_the_deployment_s_executor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    assert deployment_default_type("runtime") == "k8s_job"


def test_the_gateway_is_the_deployment_s_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_GATEWAY_TYPE", "litellm")
    assert deployment_default_type("gateway") == "litellm"


def test_the_memory_follows_its_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CHOREGOS_MEMORY_URL", raising=False)
    assert deployment_default_type("memory") == "lexical"
    monkeypatch.setenv("CHOREGOS_MEMORY_URL", "http://ecphoria:8432")
    assert deployment_default_type("memory") == "ecphoria"


@pytest.mark.parametrize("kind", ["cd", "ci", "notify", "tracker", "scm", "identity", "documents"])
def test_nothing_else_is_provided_by_a_deployment(kind: str) -> None:
    """`argocd` built with an empty configuration has no GitOps repository and no token: it is not a CD."""
    assert deployment_default_type(kind) is None
