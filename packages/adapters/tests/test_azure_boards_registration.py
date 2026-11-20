"""The `azure-boards` tracker type declares itself: spec, validation, egress, account, factory (S13-07)."""

from __future__ import annotations

from typing import Any

import pytest
from tranibor_adapters import account_of, connector_types, resolved_configuration, spec_of
from tranibor_adapters.builtins import _azure_boards_tracker
from tranibor_adapters.errors import ConfigurationError
from tranibor_adapters.tracker.azure_boards import AzureBoardsTracker, AzureDevOpsClient


@pytest.fixture(autouse=True)
def _no_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under TRANIBOR_FAKES a plain-http address and an Entra host off the Services are allowed."""
    monkeypatch.delenv("TRANIBOR_FAKES", raising=False)


GOOD: dict[str, Any] = {"base_url": "https://dev.azure.com/northwind", "project": "Billing", "pat": "x"}


def test_the_type_is_registered_with_its_secrets_declared() -> None:
    types = {(k, t): spec for k, t, spec in connector_types()}
    spec = types[("tracker", "azure-boards")]
    assert spec.capabilities == ("tracker",)
    assert set(spec.secret_fields) == {"pat", "client_secret", "webhook_secret"}
    assert not set(spec.secret_fields) & set(spec.config_schema["properties"])
    assert spec.config_schema["required"] == ["base_url", "project"]


def test_a_secret_is_a_reference_resolved_when_the_adapter_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECTOR_AZURE_PAT", "s3cr3t")
    config = resolved_configuration(
        "tracker", "azure-boards", {"project": "B"}, {"pat": "env:CONNECTOR_AZURE_PAT"}
    )
    assert config == {"project": "B", "pat": "s3cr3t"}


def test_the_factory_builds_a_pat_tracker_and_an_entra_one() -> None:
    pat = _azure_boards_tracker({**GOOD, "area_path": "Billing\\A", "candidates_wiql": "[System.Id] > 1"})
    assert isinstance(pat, AzureBoardsTracker) and isinstance(pat.client, AzureDevOpsClient)
    assert pat.area_path == "Billing\\A" and pat.client.entra is None
    entra = _azure_boards_tracker(
        {**GOOD, "pat": None, "auth": "entra", "tenant_id": "t", "client_id": "c", "client_secret": "s"}
    )
    assert entra.client.entra is not None and entra.client.entra.client_id == "c"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"base_url": ""}, "base_url"),
        ({"base_url": "http://dev.azure.com/x"}, "https"),
        ({"project": " "}, "project"),
        ({"auth": "oauth"}, "auth"),
        ({"auth": "entra", "tenant_id": "t"}, "client_id"),
        ({"auth": "entra", "client_id": "c"}, "tenant_id"),
        (
            {"auth": "entra", "tenant_id": "t", "client_id": "c", "base_url": "https://tfs.corp/tfs/Default"},
            "Services",
        ),
    ],
)
def test_a_bad_configuration_is_refused_by_name(change: dict[str, Any], message: str) -> None:
    spec = spec_of("tracker", "azure-boards")
    assert spec is not None and spec.check_config is not None
    with pytest.raises(ConfigurationError, match=message):
        spec.check_config({**GOOD, **change})
    with pytest.raises(ConfigurationError, match=message):
        _azure_boards_tracker({**GOOD, **change})


def test_a_server_collection_and_a_service_principal_on_the_services_pass() -> None:
    spec = spec_of("tracker", "azure-boards")
    assert spec is not None and spec.check_config is not None
    spec.check_config({**GOOD, "base_url": "https://tfs.corp.example/tfs/DefaultCollection"})
    spec.check_config({**GOOD, "auth": "entra", "tenant_id": "t", "client_id": "c"})
    spec.check_config(
        {
            **GOOD,
            "auth": "entra",
            "tenant_id": "t",
            "client_id": "c",
            "base_url": "https://n.visualstudio.com",
        }
    )


def test_the_egress_is_the_collection_host_and_the_login_host_for_a_service_principal() -> None:
    spec = spec_of("tracker", "azure-boards")
    assert spec is not None and spec.egress is not None
    assert spec.egress(GOOD) == [("dev.azure.com", 443)]
    assert spec.egress({"base_url": "https://tfs.corp.example:8443/tfs/Default"}) == [
        ("tfs.corp.example", 8443)
    ]
    assert spec.egress({**GOOD, "auth": "entra"}) == [
        ("dev.azure.com", 443),
        ("login.microsoftonline.com", 443),
    ]


def test_a_service_principal_writes_as_its_client_id_and_a_pat_names_nobody() -> None:
    assert account_of("tracker", "azure-boards", {"auth": "entra", "client_id": "c-1"}) == "c-1"
    assert account_of("tracker", "azure-boards", {"auth": "pat"}) is None
