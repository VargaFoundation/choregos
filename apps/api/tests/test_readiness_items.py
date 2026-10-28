"""One test per readiness item and state (S40-06): pure functions over `Facts`, no database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from choregos_adapters.executor.agent_sandbox import runtime_class_refusal
from choregos_api.services.readiness import (
    ConnectorFact,
    Facts,
    ObjectStoreFact,
    RuntimeFact,
    TemplateWorkflowFact,
    evaluate,
    item_backend,
    item_budgets,
    item_connector_secrets,
    item_connectors,
    item_first_work_item,
    item_github_app,
    item_models,
    item_notifications,
    item_object_store,
    item_people,
    item_provisioned,
    item_repository,
    item_runtime_class,
    item_stale_tests,
    item_tools,
    item_workflow,
    item_workflow_template,
    runtime_fact,
)
from choregos_contracts import Policy, ProjectConfig

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=UTC)


def facts(**changes: Any) -> Facts:
    config = changes.pop("config", None) or ProjectConfig.model_validate({"slug": "dev", "org": "varga"})
    base: dict[str, Any] = {
        "slug": "dev",
        "status": "active",
        "provision_failure": None,
        "config": config,
        "workflows": ("dev-simple",),
        "default_workflow": "dev-simple",
        "requirements": (),
        "connectors": {},
        "provided": frozenset({"runtime", "gateway", "memory", "tracker"}),
        "now": NOW,
    }
    return Facts(**{**base, **changes})


def connector(kind: str, type_: str = "argocd", status: str = "ok", **changes: Any) -> ConnectorFact:
    fields: dict[str, Any] = {
        "kind": kind,
        "type": type_,
        "status": status,
        "last_check_at": NOW - timedelta(hours=1),
        "last_error": None,
        "config": {},
    }
    return ConnectorFact(**{**fields, **changes})


def states(items: list[Any]) -> dict[str, str]:
    return {i.id: i.state for i in items}


# ── provisioning, workflow ──


def test_a_draft_project_is_to_do_and_names_the_failing_step() -> None:
    (item,) = item_provisioned(facts(status="draft", provision_failure="argocd.wait_synced: timeout"))
    assert item.state == "todo" and "argocd.wait_synced: timeout" in item.detail
    assert item_provisioned(facts())[0].state == "done"
    assert item_provisioned(facts(status="provisioning"))[0].state == "todo"


def test_an_inactive_project_points_at_the_gesture_that_activates_it() -> None:
    (draft,) = item_provisioned(facts(status="draft"))
    assert draft.fix.href == "/p/dev/settings#general" and "activate it" in draft.detail
    (suspended,) = item_provisioned(facts(status="suspended"))
    assert "resume it" in suspended.detail


def test_no_active_workflow_is_to_do_and_no_default_is_to_check() -> None:
    assert item_workflow(facts(workflows=(), default_workflow=None))[0].state == "todo"
    assert item_workflow(facts(default_workflow=None))[0].state == "check"
    assert item_workflow(facts())[0].state == "done"


# ── connectors ──


def test_a_demo_cd_is_to_check_not_done() -> None:
    f = facts(
        requirements=(("cd", ("a release train needs it",)),), connectors={"cd": connector("cd", "demo")}
    )
    assert states(item_connectors(f)) == {"connector-cd": "check"}


def test_a_required_cd_without_connector_is_to_do() -> None:
    """The client-side list said "done" for any capability with a default type, a fake included."""
    f = facts(requirements=(("cd", ("a release train needs it",)),))
    (item,) = item_connectors(f)
    assert item.state == "todo" and item.fix.by == "project_owner"


def test_runtime_provided_by_the_deployment_is_done() -> None:
    f = facts(requirements=(("runtime", ("agents run somewhere",)),))
    (item,) = item_connectors(f)
    assert item.state == "done" and item.fix.by == "platform_operator"
    assert "provided by the platform" in item.detail


def test_a_connector_is_done_when_its_last_test_passed_to_check_when_never_tested_to_do_when_failed() -> None:
    required = (("scm", ("an agent works in a repository",)),)
    ok = facts(requirements=required, connectors={"scm": connector("scm", "github", "ok")})
    never = facts(requirements=required, connectors={"scm": connector("scm", "github", "unknown")})
    broken = facts(
        requirements=required,
        connectors={"scm": connector("scm", "github", "error", last_error="403 Resource not accessible")},
    )
    assert item_connectors(ok)[0].state == "done"
    assert item_connectors(never)[0].state == "check"
    failed = item_connectors(broken)[0]
    assert failed.state == "todo" and "403 Resource not accessible" in failed.detail


def test_a_missing_secret_is_to_do_and_a_set_one_is_done() -> None:
    missing = connector("notify", "slack", secrets_expected=("bot_token",))
    present = connector(
        "notify", "slack", secrets_expected=("bot_token",), secrets_set=frozenset({"bot_token"})
    )
    assert states(item_connector_secrets(facts(connectors={"notify": missing}))) == {
        "connector-notify-secrets": "todo"
    }
    assert states(item_connector_secrets(facts(connectors={"notify": present}))) == {
        "connector-notify-secrets": "done"
    }
    assert item_connector_secrets(facts(connectors={"scm": connector("scm", "github")})) == []


def test_stale_test_is_to_check() -> None:
    old = connector("scm", "github", last_check_at=NOW - timedelta(days=8))
    fresh = connector("cd", "argocd", last_check_at=NOW - timedelta(days=6))
    broken = connector("ci", "tekton", "error", last_check_at=NOW - timedelta(days=30))
    got = states(item_stale_tests(facts(connectors={"scm": old, "cd": fresh, "ci": broken})))
    assert got == {"connector-scm-stale": "check"}, (
        "only a passing test that is old; a failing one is already to do"
    )


def test_a_github_owner_without_a_claimed_installation_is_to_do() -> None:
    owners = (("scm", "stranger", False), ("tracker", "varga", True))
    got = states(item_github_app(facts(github_app=True, github_owners=owners)))
    assert got == {"github-app-scm": "todo", "github-app-tracker": "done"}
    assert states(item_github_app(facts(github_app=False, github_owners=owners))) == {
        "github-app-scm": "not_needed",
        "github-app-tracker": "not_needed",
    }


# ── repository, backend, models, budgets ──


def test_no_test_command_is_to_check() -> None:
    required = (("scm", ("an agent works in a repository",)),)
    bare = ProjectConfig.model_validate(
        {"slug": "dev", "org": "varga", "repo": {"url": "https://github.com/a/b"}}
    )
    measured = ProjectConfig.model_validate(
        {
            "slug": "dev",
            "org": "varga",
            "repo": {"url": "https://github.com/a/b", "test_command": "pytest -q"},
        }
    )
    assert states(item_repository(facts(requirements=required, config=bare))) == {
        "repository": "done",
        "repository-checks": "check",
    }
    assert (
        states(item_repository(facts(requirements=required, config=measured)))["repository-checks"] == "done"
    )
    assert states(item_repository(facts(requirements=required)))["repository"] == "todo", "no URL"
    assert item_repository(facts()) == [], "no scm required: nothing to say about a repository"


def test_without_a_repository_the_workflow_that_asks_for_one_is_named_and_can_be_changed() -> None:
    required = (
        ("scm", ("default-simple: the agent dev (implement) works in a repository",)),
        ("cd", ("default-simple: the state deployed_prod is production",)),
    )
    (repository,) = item_repository(facts(requirements=required))
    assert repository.state == "todo" and "default-simple: the agent dev" in repository.detail
    assert "a workflow that does not deliver code" in repository.detail
    scm, cd = item_connectors(facts(requirements=required))
    assert scm.state == cd.state == "todo"
    gesture = "/p/dev/workflows/new?template=default-request&default=1"
    assert scm.fix.href == cd.fix.href == repository.fix.href == gesture, "the way out is another workflow"
    published = facts(requirements=required, workflows=("default-simple", "default-request"))
    assert item_repository(published)[0].fix.href == "/p/dev/workflows", "published: make it the default"
    with_repo = ProjectConfig.model_validate(
        {"slug": "dev", "org": "varga", "repo": {"url": "https://github.com/varga/dev.git"}}
    )
    (scm,) = item_connectors(facts(requirements=required[:1], config=with_repo))
    assert scm.fix.href == "/p/dev/settings#connections", "a project with a repository connects it"


def test_the_dod_facts_count_as_measured_checks() -> None:
    config = ProjectConfig.model_validate(
        {
            "slug": "dev",
            "org": "varga",
            "repo": {"url": "https://github.com/a/b"},
            "dod": {"facts": {"ok": "true"}},
        }
    )
    got = states(item_repository(facts(requirements=(("scm", ("x",)),), config=config)))
    assert got["repository-checks"] == "done"


def test_a_backend_the_deployment_does_not_enable_is_to_do() -> None:
    config = ProjectConfig.model_validate(
        {"slug": "dev", "org": "varga", "agent": {"default_backend": "opencode"}}
    )
    assert item_backend(facts(config=config, usable_backends=frozenset({"claude-code"})))[0].state == "todo"
    assert item_backend(facts(config=config, usable_backends=frozenset({"opencode"})))[0].state == "done"


def test_an_unresolvable_profile_is_to_do() -> None:
    assert item_models(facts(profiles_used=2, profiles_unknown=("creative",)))[0].state == "todo"


def test_an_unproven_pairing_is_to_check_unless_unvalidated_is_allowed() -> None:
    f = facts(profiles_used=1, profiles_unvalidated=("standard",))
    assert item_models(f)[0].state == "check"
    allowed = ProjectConfig.model_validate(
        {"slug": "dev", "org": "varga", "models": {"allow_unvalidated": True}}
    )
    assert (
        item_models(facts(profiles_used=1, profiles_unvalidated=("standard",), config=allowed))[0].state
        == "done"
    )
    assert item_models(facts())[0].state == "not_needed", "no workflow names a profile"
    assert item_models(facts(profiles_used=1))[0].state == "done"


def test_budgets_are_checked_until_both_are_set() -> None:
    assert item_budgets(facts())[0].state == "check"
    assert item_budgets(facts(ticket_budget_set=True))[0].state == "check"
    assert item_budgets(facts(ticket_budget_set=True, daily_budget_set=True))[0].state == "done"


# ── people, notifications, tools, first item ──


def test_an_unknown_human_group_is_to_check() -> None:
    got = states(item_people(facts(has_owner=True, unknown_groups=("release-captains",))))
    assert got == {"people-owner": "done", "people-group-release-captains": "check"}
    group = item_people(facts(has_owner=True, unknown_groups=("pm",)))[1]
    assert group.fix.by == "external", "an identity provider's group is not ours to create"


def test_a_release_train_without_a_captain_is_to_do() -> None:
    assert states(item_people(facts(has_owner=True, has_release_train=True))) == {
        "people-owner": "done",
        "people-release-captain": "todo",
    }
    assert "people-release-captain" not in states(item_people(facts(has_owner=True))), "no train, no captain"
    assert item_people(facts())[0].state == "todo", "nobody owns the project"


def test_a_train_without_notify_is_to_do() -> None:
    (item,) = item_notifications(facts(has_release_train=True))
    assert item.state == "todo" and item.section == "notifications"


def test_announcements_without_a_train_are_only_to_check() -> None:
    assert item_notifications(facts(announces=True))[0].state == "check"
    assert item_notifications(facts())[0].state == "not_needed"


def test_a_notify_connector_without_a_channel_is_to_do_then_done_with_one() -> None:
    slack = connector("notify", "slack", "ok")
    assert item_notifications(facts(has_release_train=True, connectors={"notify": slack}))[0].state == "todo"
    with_channel = ProjectConfig.model_validate(
        {"slug": "dev", "org": "varga", "notify": {"slack_channel": "#choregos"}}
    )
    got = item_notifications(facts(has_release_train=True, connectors={"notify": slack}, config=with_channel))
    assert got[0].state == "done"
    untested = connector("notify", "slack", "unknown")
    got = item_notifications(
        facts(has_release_train=True, connectors={"notify": untested}, config=with_channel)
    )
    assert got[0].state == "check"


def test_tools_are_informational_never_to_do() -> None:
    assert item_tools(facts())[0].state == "done"
    opened = ProjectConfig.model_validate({"slug": "dev", "org": "varga", "tools": ["*"]})
    assert "whole catalogue" in item_tools(facts(config=opened))[0].detail


def test_a_first_request_is_checked_until_one_is_filed() -> None:
    assert item_first_work_item(facts())[0].state == "check"
    assert item_first_work_item(facts(work_items=3))[0].state == "done"


# ── the whole ──


def test_ready_means_no_item_is_to_do_and_the_summary_counts_the_states() -> None:
    ok = facts(has_owner=True, work_items=1, ticket_budget_set=True, daily_budget_set=True)
    result = evaluate(ok)
    assert result.ready is True and result.summary.todo == 0
    assert result.summary.done + result.summary.check == len(
        [i for i in result.items if i.state != "not_needed"]
    )
    broken = evaluate(facts(status="draft", has_owner=True))
    assert broken.ready is False and broken.summary.todo == 1
    assert all(
        i.fix.by in {"project_owner", "org_admin", "platform_operator", "external"} for i in broken.items
    )
    assert len({i.id for i in broken.items}) == len(broken.items), "item ids are unique"


# ── object store (S38-13c) ──


def test_a_workflow_with_no_file_output_asks_nothing_of_the_object_store() -> None:
    assert item_object_store(facts()) == []


def test_a_file_output_and_a_store_that_fails_its_probe_is_to_do_naming_the_cause() -> None:
    failed = ObjectStoreFact(error="object store https://s3.example: the store refused the credentials")
    (item,) = item_object_store(facts(file_outputs=("bars", "report"), object_store=failed))
    assert item.state == "todo" and item.fix.by == "platform_operator"
    assert "`bars`" in item.detail and "refused the credentials" in item.detail


def test_a_file_output_and_a_store_that_answers_is_done() -> None:
    (item,) = item_object_store(facts(file_outputs=("bars",), object_store=ObjectStoreFact()))
    assert item.state == "done"


# ── warm pool runtime (S38-13d) ──


def _policy(runtime: str) -> Policy:
    return Policy.model_validate({"metadata": {"name": "p", "version": "1"}, "sandbox": {"runtime": runtime}})


@pytest.fixture
def real_deployment(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    monkeypatch.setenv("CHOREGOS_FAKES", "0")
    monkeypatch.setenv("CHOREGOS_EXECUTOR_KIND", "agent_sandbox")
    monkeypatch.delenv("CHOREGOS_RUNNER_RUNTIME_CLASS", raising=False)
    monkeypatch.delenv("CHOREGOS_SANDBOX_WARM_POOL", raising=False)
    return monkeypatch


def test_the_executor_and_the_readiness_item_share_one_comparison() -> None:
    assert runtime_class_refusal("gvisor", "gvisor", "pool") is None
    assert runtime_class_refusal(None, "gvisor", "pool") is None
    message = runtime_class_refusal("gvisor", "", "pool")
    assert message is not None and "`gvisor`" in message and "cluster default" in message
    assert "runner.runtimeClass" in message


def test_a_gvisor_policy_on_a_pool_with_the_cluster_default_is_to_do(
    real_deployment: pytest.MonkeyPatch,
) -> None:
    real_deployment.setenv("CHOREGOS_RUNNER_RUNTIME_CLASS", "")
    fact = runtime_fact({}, _policy("gvisor"))
    assert fact is not None and fact.refusal is not None
    (item,) = item_runtime_class(facts(runtime=fact))
    assert item.state == "todo" and item.fix.by == "platform_operator"
    for wanted in (
        "sandbox.runtime: gvisor",
        "choregos-runner",
        "the cluster default",
        "runner.runtimeClass",
    ):
        assert wanted in item.detail
    assert evaluate(facts(runtime=fact)).ready is False


def test_a_gvisor_policy_on_a_gvisor_pool_is_done(real_deployment: pytest.MonkeyPatch) -> None:
    real_deployment.setenv("CHOREGOS_RUNNER_RUNTIME_CLASS", "gvisor")
    (item,) = item_runtime_class(facts(runtime=runtime_fact({}, _policy("gvisor"))))
    assert item.state == "done"


def test_nothing_is_judged_when_it_cannot_be_judged(real_deployment: pytest.MonkeyPatch) -> None:
    """No false alarm: a policy that asks nothing, an executor that is not the warm pool, fakes,
    and a deployment that never said which RuntimeClass its pool runs."""
    gvisor, default = _policy("gvisor"), _policy("default")
    real_deployment.setenv("CHOREGOS_RUNNER_RUNTIME_CLASS", "")
    assert runtime_fact({}, default) is None
    real_deployment.setenv("CHOREGOS_EXECUTOR_KIND", "k8s_job")
    assert runtime_fact({}, gvisor) is None, "a Job sets runtimeClassName per run"
    real_deployment.setenv("CHOREGOS_EXECUTOR_KIND", "agent_sandbox")
    job = connector("runtime", "k8s_job")
    assert runtime_fact({"runtime": job}, gvisor) is None, "the project's own runtime connector decides"
    real_deployment.setenv("CHOREGOS_FAKES", "1")
    assert runtime_fact({}, gvisor) is None
    real_deployment.setenv("CHOREGOS_FAKES", "0")
    real_deployment.delenv("CHOREGOS_RUNNER_RUNTIME_CLASS")
    assert runtime_fact({}, gvisor) is None, "unknown pool runtime: no judgment"
    assert item_runtime_class(facts()) == []


def test_a_sandbox_connector_with_its_own_runtime_class_is_judged_on_it(
    real_deployment: pytest.MonkeyPatch,
) -> None:
    real_deployment.setenv("CHOREGOS_RUNNER_RUNTIME_CLASS", "")
    pool = connector("runtime", "agent_sandbox", config={"runtime_class": "gvisor", "warm_pool": "mine"})
    fact = runtime_fact({"runtime": pool}, _policy("gvisor"))
    assert fact == RuntimeFact("gvisor", None)


def _campaign(stored: str, shipped: str) -> TemplateWorkflowFact:
    return TemplateWorkflowFact("campaign", 1, stored, shipped, "./workflows/campaign.yaml")


def test_a_workflow_that_differs_from_what_its_template_ships_now_is_to_check_naming_both() -> None:
    old, new = "sha256:" + "a" * 64, "sha256:" + "b" * 64
    f = facts(template="investment-research@0.1.0", template_workflows=(_campaign(old, new),))
    (item,) = item_workflow_template(f)
    assert item.id == "workflow-template" and item.state == "check" and item.fix.by == "project_owner"
    assert "`campaign` v1" in item.detail and "`./workflows/campaign.yaml`" in item.detail
    assert "a" * 12 in item.detail and "b" * 12 in item.detail
    assert "choregos workflow push" in item.detail and "source: template" in item.detail
    assert item.fix.href == "/p/dev/workflows"


def test_a_workflow_equal_to_what_its_template_ships_is_done() -> None:
    same = "sha256:" + "c" * 64
    (item,) = item_workflow_template(
        facts(template="investment-research", template_workflows=(_campaign(same, same),))
    )
    assert item.state == "done" and "`campaign`" in item.detail


def test_no_template_or_no_workflow_it_still_ships_asks_nothing() -> None:
    assert item_workflow_template(facts()) == []
    assert item_workflow_template(facts(template="investment-research")) == []
