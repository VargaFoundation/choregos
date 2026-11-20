"""The `azure-repos` and `azure-pipelines` types declare themselves, and their hooks are read (S13-08)."""

from __future__ import annotations

import json
from typing import Any

import pytest
from tranibor_adapters import account_of, connector_types, resolved_configuration
from tranibor_adapters.azure_devops import AzureDevOpsClient, azure_egress, shared_secret_of
from tranibor_adapters.builtins import _azure_pipelines_ci, _azure_repos_scm
from tranibor_adapters.ci.azure_pipelines import AzurePipelinesCi
from tranibor_adapters.errors import ConfigurationError
from tranibor_adapters.scm.azure_repos import AzureReposScm


@pytest.fixture(autouse=True)
def _no_fakes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under TRANIBOR_FAKES a plain-http address and an Entra host off the Services are allowed."""
    monkeypatch.delenv("TRANIBOR_FAKES", raising=False)


GOOD: dict[str, Any] = {
    "base_url": "https://dev.azure.com/northwind",
    "project": "Billing",
    "repository": "billing-api",
    "pat": "x",
}
ENTRA: dict[str, Any] = {
    **GOOD,
    "pat": None,
    "auth": "entra",
    "tenant_id": "t",
    "client_id": "c",
    "client_secret": "s",
}


def types() -> dict[tuple[str, str], Any]:
    return {(k, t): spec for k, t, spec in connector_types()}


def test_the_types_are_registered_with_their_secrets_declared() -> None:
    scm, ci = types()[("scm", "azure-repos")], types()[("ci", "azure-pipelines")]
    assert scm.capabilities == ("scm",) and ci.capabilities == ("ci",)
    assert set(scm.secret_fields) == {"pat", "client_secret", "run_token"}
    assert set(ci.secret_fields) == {"pat", "client_secret"}
    for spec in (scm, ci):
        assert not set(spec.secret_fields) & set(spec.config_schema["properties"])
    assert scm.config_schema["required"] == ["base_url", "project", "repository"]
    assert ci.config_schema["required"] == ["base_url", "project"]


def test_a_secret_is_a_reference_resolved_when_the_adapter_is_built(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CONNECTOR_AZ_PAT", "s3cr3t")
    config = resolved_configuration("scm", "azure-repos", {"project": "B"}, {"pat": "env:CONNECTOR_AZ_PAT"})
    assert config == {"project": "B", "pat": "s3cr3t"}


def test_the_factories_build_a_pat_adapter_and_an_entra_one() -> None:
    scm = _azure_repos_scm({**GOOD, "merge_strategy": "rebase", "run_token": "rt", "default_branch": "dev"})
    assert isinstance(scm, AzureReposScm) and isinstance(scm.client, AzureDevOpsClient)
    assert (scm.merge_strategy, scm.run_token, scm.default_branch, scm.client.entra) == (
        "rebase",
        "rt",
        "dev",
        None,
    )
    ci = _azure_pipelines_ci({**GOOD, "pipeline": "ci", "partially_succeeded": "neutral"})
    assert isinstance(ci, AzurePipelinesCi) and (ci.pipeline, ci.repository, ci.partial) == (
        "ci",
        "billing-api",
        "neutral",
    )
    entra = _azure_repos_scm(ENTRA)
    assert entra.client.entra is not None and entra.client.entra.client_id == "c"


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"base_url": ""}, "base_url"),
        ({"base_url": "http://dev.azure.com/x"}, "https"),
        ({"project": " "}, "project"),
        ({"repository": ""}, "repository"),
        ({"auth": "oauth"}, "auth"),
        ({"merge_strategy": "fast"}, "merge_strategy"),
        ({"partially_succeeded": "pass"}, "partially_succeeded"),
        ({"auth": "entra", "tenant_id": "t"}, "client_id"),
        ({"auth": "entra", "client_id": "c"}, "tenant_id"),
        (
            {"auth": "entra", "tenant_id": "t", "client_id": "c", "base_url": "https://tfs.corp/tfs/Default"},
            "Server",
        ),
    ],
)
def test_a_bad_configuration_is_refused_on_save(change: dict[str, Any], message: str) -> None:
    from tranibor_adapters.azure_devops import check_azure_repos

    with pytest.raises(ConfigurationError, match=message):
        check_azure_repos({**GOOD, **change})


def test_the_pipelines_check_does_not_need_a_repository() -> None:
    from tranibor_adapters.azure_devops import check_azure_pipelines

    check_azure_pipelines({k: v for k, v in GOOD.items() if k != "repository"})
    with pytest.raises(ConfigurationError, match="project"):
        check_azure_pipelines({**GOOD, "project": ""})


def test_a_server_takes_a_pat_on_its_own_collection() -> None:
    from tranibor_adapters.azure_devops import check_azure_repos

    check_azure_repos({**GOOD, "base_url": "https://tfs.corp/tfs/Default"})


def test_both_a_pat_and_none_are_refused_by_the_client() -> None:
    with pytest.raises(ConfigurationError):
        AzureDevOpsClient("https://dev.azure.com/x")
    with pytest.raises(ConfigurationError):
        _azure_repos_scm({**GOOD, "pat": ""})


def test_the_egress_hosts_are_what_the_connector_calls() -> None:
    spec = types()[("scm", "azure-repos")]
    assert spec.egress is not None
    assert azure_egress(GOOD) == [("dev.azure.com", 443), ("vssps.dev.azure.com", 443)]
    assert ("login.microsoftonline.com", 443) in azure_egress(ENTRA)
    assert azure_egress({**GOOD, "base_url": "https://tfs.corp/tfs/Default"}) == [("tfs.corp", 443)]
    assert types()[("ci", "azure-pipelines")].egress is azure_egress or callable(
        types()[("ci", "azure-pipelines")].egress
    )


def test_the_account_of_a_service_principal_is_its_client_id() -> None:
    assert account_of("scm", "azure-repos", ENTRA) == "c"
    assert account_of("ci", "azure-pipelines", ENTRA) == "c"
    assert account_of("scm", "azure-repos", GOOD) is None


def test_the_hook_secret_is_the_basic_password_or_the_header() -> None:
    import base64

    basic = "Basic " + base64.b64encode(b"hook:s3cret").decode()
    assert shared_secret_of({"Authorization": basic}) == "s3cret"
    assert shared_secret_of({"authorization": basic}) == "s3cret"
    assert shared_secret_of({"X-Choregos-Secret": "h"}) == "h"
    assert shared_secret_of({"Authorization": "Bearer abc"}) == ""
    assert shared_secret_of({"Authorization": "Basic !!!"}) == ""
    assert shared_secret_of({}) == ""


def pr_event(kind: str, status: str = "active", **resource: Any) -> bytes:
    return json.dumps(
        {
            "notificationId": 21,
            "eventType": kind,
            "resource": {
                "pullRequestId": 5,
                "status": status,
                "title": "Fix",
                "isDraft": False,
                "sourceRefName": "refs/heads/choregos/12-fix",
                "targetRefName": "refs/heads/main",
                "lastMergeSourceCommit": {"commitId": "a" * 40},
                "createdBy": {"uniqueName": "bot@northwind.test"},
                "labels": [{"name": "agent"}],
                "repository": {"name": "billing-api", "project": {"name": "Billing"}},
                **resource,
            },
            "resourceContainers": {"collection": {"baseUrl": "https://dev.azure.com/northwind/"}},
        }
    ).encode()


def scm() -> AzureReposScm:
    return _azure_repos_scm(GOOD)  # type: ignore[no-any-return]


def test_a_created_pull_request_opens_one_for_the_work_item() -> None:
    (event,) = scm().parse_webhook({}, pr_event("git.pullrequest.created"))
    assert event.type.value == "scm.pr.opened"
    assert (event.work_item_key, event.project_slug, event.actor) == ("12", "billing", "bot@northwind.test")
    assert event.payload["sha"] == "a" * 40 and event.payload["head"] == "choregos/12-fix"
    assert event.payload["pr_url"] == "https://dev.azure.com/northwind/Billing/_git/billing-api/pullrequest/5"
    assert event.payload["merged"] is False and event.payload["labels"] == ["agent"]


def test_an_update_synchronizes_a_completion_merges_and_an_abandon_closes() -> None:
    kinds = {
        ("git.pullrequest.updated", "active"): "scm.pr.synchronized",
        ("git.pullrequest.updated", "completed"): "scm.pr.merged",
        ("git.pullrequest.merged", "completed"): "scm.pr.merged",
        ("git.pullrequest.updated", "abandoned"): "scm.pr.closed",
    }
    for (event_type, status), expected in kinds.items():
        assert [e.type.value for e in scm().parse_webhook({}, pr_event(event_type, status))] == [expected]
    # A merge attempt of an active pull request is not a merge.
    assert scm().parse_webhook({}, pr_event("git.pullrequest.merged", "active")) == []


def test_a_branch_that_names_no_work_item_has_no_key_and_ci_events_are_not_scm_events() -> None:
    (event,) = scm().parse_webhook(
        {}, pr_event("git.pullrequest.created", sourceRefName="refs/heads/feature/x")
    )
    assert event.work_item_key is None
    build = json.dumps({"eventType": "build.complete", "resource": {"result": "succeeded"}}).encode()
    assert scm().parse_webhook({}, build) == []
    assert scm().parse_webhook({}, b"") == []
