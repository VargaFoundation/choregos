"""The connector types declare what they are (ADR 0034, S19-01).

The registry is the only source: the list the API served lived in duplicate, and called jira
"unavailable" while it is registered. A secret is only written as a reference, resolved when
the adapter is built.
"""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters import (
    AdapterSet,
    connector_types,
    default_type,
    register,
    resolved_configuration,
    spec_of,
)
from choregos_adapters.registry import _REGISTRY, _SPECS
from choregos_core.secrets import SecretNotFound


def test_every_shipped_type_declares_itself_and_no_fake_is_shown() -> None:
    types = {(k, t): spec for k, t, spec in connector_types()}
    assert ("tracker", "jira") in types, "jira is registered: it is available"
    assert not [t for (_, t) in types if t == "fake"]
    for (kind, type_name), spec in types.items():
        assert kind in spec.capabilities, f"{kind}/{type_name} does not declare its capability"
        assert spec.config_schema.get("type") == "object", f"{kind}/{type_name} has no schema"


def test_the_known_secrets_are_declared_as_such() -> None:
    """What the factories read as a secret (Jira's API token, Argo CD's,
    LiteLLM's master key) must be a secret field, never a key of `config`."""
    assert "api_token" in spec_of("tracker", "jira").secret_fields  # type: ignore[union-attr]
    assert "token" in spec_of("cd", "argocd").secret_fields  # type: ignore[union-attr]
    assert "master_key" in spec_of("gateway", "litellm").secret_fields  # type: ignore[union-attr]
    for _, _, spec in connector_types():
        assert not set(spec.secret_fields) & set(spec.config_schema.get("properties", {})), spec.display


def test_a_reference_is_resolved_field_by_field(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECTOR_JIRA_TOKEN", "s3cr3t")
    config = resolved_configuration(
        "tracker", "jira", {"project_key": "RH"}, {"api_token": "env:CONNECTOR_JIRA_TOKEN"}
    )
    assert config == {"project_key": "RH", "api_token": "s3cr3t"}
    # `secret_ref`, from before the ADR: it stands for the type's first secret field.
    assert (
        resolved_configuration("tracker", "jira", {}, None, "env:CONNECTOR_JIRA_TOKEN")["api_token"]
        == "s3cr3t"
    )


def test_a_preresolved_field_is_not_resolved_again(monkeypatch: pytest.MonkeyPatch) -> None:
    """The platform reads `store:` values with the organisation's context (ADR 0058) and hands them
    over; the sync resolver of the core, which cannot, is never asked for them."""
    monkeypatch.setenv("CONNECTOR_JIRA_EMAIL_TOKEN", "from-env")
    refs = {"api_token": "store:connector/jira/api_token", "webhook_secret": "env:CONNECTOR_JIRA_EMAIL_TOKEN"}
    config = resolved_configuration("tracker", "jira", {}, refs, preresolved={"api_token": "sealed"})
    assert config == {"api_token": "sealed", "webhook_secret": "from-env"}
    with pytest.raises(SecretNotFound, match="resolved by the platform"):
        resolved_configuration("tracker", "jira", {}, refs)


def test_an_unresolvable_reference_stops_the_construction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Better than an adapter that silently falls back to its default value."""
    monkeypatch.delenv("CONNECTOR_ARGO_TOKEN", raising=False)
    with pytest.raises(SecretNotFound, match="CONNECTOR_ARGO_TOKEN"):
        resolved_configuration("cd", "argocd", {}, {"token": "env:CONNECTOR_ARGO_TOKEN"})


def test_a_projects_adapters_receive_their_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Through the orchestrator's path: `from_connectors` resolves before building."""
    kind_names = ("tracker", "scm", "ci", "cd", "runtime", "memory", "gateway", "notify")
    received: dict[str, dict[str, Any]] = {}
    try:
        for kind_name in kind_names:
            register(kind_name, "probe")(lambda cfg, s=kind_name: received.__setitem__(s, cfg) or object())
        monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
        monkeypatch.setenv("CONNECTOR_PROBE_TOKEN", "t0k")
        connectors: dict[str, dict[str, Any]] = {s: {"type": "probe"} for s in kind_names}
        connectors["notify"] = {
            "type": "probe",
            "config": {"channel": "#rh"},
            "secret_refs": {"bot_token": "env:CONNECTOR_PROBE_TOKEN"},
        }
        AdapterSet.from_connectors(connectors)
    finally:
        for kind_name in kind_names:
            _REGISTRY.pop((kind_name, "probe"), None)
            _SPECS.pop((kind_name, "probe"), None)
    assert received["notify"] == {"channel": "#rh", "bot_token": "t0k"}
    assert received["scm"] == {}


def test_the_default_type_is_the_one_a_project_takes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    assert default_type("runtime") == "k8s_job"
    assert default_type("scm") == "github"
    assert default_type("identity") is None


def test_github_types_never_ask_an_installation_id() -> None:
    """The installation is discovered and claimed by the platform (ADR 0073): a form that asks for
    its id asks for something nobody knows and that another company cannot fill in."""
    types = {(k, t): spec for k, t, spec in connector_types()}
    for key in (("tracker", "github-issues"), ("scm", "github")):
        properties = types[key].config_schema["properties"]
        assert "installation_id" not in properties, f"{key} still asks for an installation id"
        assert properties["repo"]["format"] == "github-repo", f"{key}: the repository is a picker"


def test_a_project_without_a_tracker_connector_gets_the_internal_tracker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#704: the default was `github-issues`, whose factory needs a `repo` nobody gave: KeyError.

    `tracker_is_internal` already reads "no tracker connector" as internal; the adapters agree.
    """
    from choregos_adapters.tracker.internal import InternalTracker

    kinds = ("scm", "ci", "cd", "runtime", "memory", "gateway", "notify")
    try:
        for kind in kinds:
            register(kind, "probe")(lambda cfg: object())
        monkeypatch.delenv("CHOREGOS_FAKES", raising=False)
        adapters = AdapterSet.from_connectors({kind: {"type": "probe"} for kind in kinds})
    finally:
        for kind in kinds:
            _REGISTRY.pop((kind, "probe"), None)
            _SPECS.pop((kind, "probe"), None)
    assert isinstance(adapters.tracker, InternalTracker)
    assert default_type("tracker") == "internal"
