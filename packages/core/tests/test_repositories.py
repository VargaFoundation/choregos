"""The repository a work item works on (S58-01, ADR 0124): its target, its refusals, when it is fixed."""

from __future__ import annotations

import pytest
from choregos_contracts import ProjectConfig
from choregos_core import parse_workflow
from choregos_core.repositories import (
    FIXED_KEY,
    RepositoryRefusedError,
    fixed_name,
    fixing,
    from_labels,
    target,
    unknown,
)

PLATFORM = ProjectConfig.model_validate(
    {
        "slug": "platform",
        "org": "acme",
        "repo": {"url": "https://github.com/acme/infra.git", "test_command": "make test"},
        "repositories": [
            {
                "name": "diamctl",
                "url": "https://github.com/acme/diamctl.git",
                "test_command": "go test ./...",
            },
            {"name": "fondation", "url": "https://github.com/acme/fondation.git"},
        ],
    }
)
SINGLE = ProjectConfig.model_validate(
    {"slug": "billing", "org": "acme", "repo": {"url": "https://github.com/acme/billing.git"}}
)
NONE = ProjectConfig.model_validate({"slug": "hr", "org": "acme"})


def test_a_work_item_that_names_no_repository_works_on_the_primary() -> None:
    found = target(PLATFORM, {}, {})
    assert found is not None and found.name == "infra" and found.repo is PLATFORM.repo
    assert target(PLATFORM, {"repository": None}, None) == found
    single = target(SINGLE, None, None)
    assert single is not None and single.repo is SINGLE.repo, "one repository: unchanged"
    assert target(NONE, {}, {}) is None, "a project without a repository: nothing to clone"


def test_a_work_item_works_on_the_repository_its_field_names() -> None:
    found = target(PLATFORM, {"repository": "diamctl"}, {})
    assert found is not None and found.name == "diamctl"
    assert found.repo.url == "https://github.com/acme/diamctl.git"
    assert found.repo.test_command == "go test ./...", "the DoD commands are the target's"
    primary = target(PLATFORM, {"repository": "infra"}, {})
    assert primary is not None and primary.repo is PLATFORM.repo, "the primary is named by its name too"


def test_an_unknown_name_is_refused_with_the_names_the_project_holds() -> None:
    with pytest.raises(
        RepositoryRefusedError, match=r"`diamclt` is not a repository.*`infra`, `diamctl`, `fondation`"
    ):
        target(PLATFORM, {"repository": "diamclt"}, {})
    with pytest.raises(RepositoryRefusedError, match="holds none"):
        target(NONE, {"repository": "diamctl"}, {})
    with pytest.raises(RepositoryRefusedError):
        target(SINGLE, {"repository": "diamctl"}, {})
    assert unknown(PLATFORM, "diamctl") is None and unknown(PLATFORM, None) is None
    assert "not a repository" in str(unknown(PLATFORM, "diamclt"))
    assert "holds no repository" in str(unknown(NONE, "diamctl"))
    assert "a string" in str(unknown(PLATFORM, ["diamctl"]))


def test_a_fixed_work_item_keeps_its_repository() -> None:
    documents = {FIXED_KEY: "diamctl"}
    kept = target(PLATFORM, {}, documents)
    assert kept is not None and kept.name == "diamctl", "fixed wins over the primary"
    same = target(PLATFORM, {"repository": "diamctl"}, documents)
    assert same is not None and same.name == "diamctl"
    with pytest.raises(RepositoryRefusedError, match="works on `diamctl` since its first step"):
        target(PLATFORM, {"repository": "fondation"}, documents)
    gone = ProjectConfig.model_validate(PLATFORM.model_dump() | {"repositories": []})
    with pytest.raises(RepositoryRefusedError, match="`diamctl` is not a repository"):
        target(gone, {}, documents)


def test_the_target_is_fixed_on_a_project_that_holds_several_only() -> None:
    found = target(PLATFORM, {"repository": "diamctl"}, {})
    assert found is not None
    assert fixing(PLATFORM, {"labels": ["a"]}, found) == {"labels": ["a"], FIXED_KEY: "diamctl"}
    assert fixing(PLATFORM, {FIXED_KEY: "diamctl"}, found) is None, "already fixed: nothing to write"
    single = target(SINGLE, {}, {})
    assert single is not None and fixing(SINGLE, {}, single) is None, "one repository: nothing recorded"


def test_a_work_item_with_a_pull_request_or_a_clone_is_fixed_on_its_current_target() -> None:
    def name(
        fields: dict[str, str], documents: dict[str, str], *, pr: bool = False, cloned: bool = False
    ) -> str | None:
        return fixed_name(PLATFORM, fields, documents, has_pull_request=pr, cloned=cloned)

    assert name({}, {}) is None, "nothing touched git: the target may still change"
    assert name({}, {FIXED_KEY: "fondation"}) == "fondation"
    assert name({"repository": "diamctl"}, {}, pr=True) == "diamctl"
    assert name({}, {}, cloned=True) == "infra", "it ran before its project held a second repository"
    assert fixed_name(NONE, {}, {}, has_pull_request=True, cloned=True) is None


def test_a_label_names_the_repository() -> None:
    assert from_labels(["agent-ready", "repository:diamctl", "repository:infra"]) == "diamctl"
    assert from_labels(["repository:", "bug"]) is None
    assert from_labels(None) is None


def test_a_workflow_may_not_declare_the_reserved_input() -> None:
    text = """
apiVersion: choregos/v1
kind: Workflow
metadata:
  name: t
  version: 1
  inputs: {type: object, properties: {repository: {type: string}}}
actors:
  ci: {type: system}
states:
  inbox: {display: Inbox}
  done: {display: Done, terminal: true}
transitions:
  - {id: t1, from: inbox, to: done, by: ci}
"""
    _, report = parse_workflow(text, strict=False)
    assert any(i.code == "inputs.reserved" and "repository" in i.message for i in report.errors), [
        i.format() for i in report.errors
    ]
    _, renamed = parse_workflow(text.replace("repository:", "target_repo:"), strict=False)
    assert not any(i.code == "inputs.reserved" for i in renamed.errors)
