"""S49-04: the workflow catalogue's contract (ADR 0108)."""

from __future__ import annotations

import choregos_contracts as contracts
import pytest
from choregos_contracts import Workflow
from choregos_core.dsl import template_yaml
from pydantic import ValidationError


def test_the_contract_declares_the_catalogue_operations() -> None:
    paths = contracts.load_openapi()["paths"]
    assert paths["/orgs/{org}/workflow-catalogue"]["get"]["operationId"] == "listWorkflowCatalogue"
    assert paths["/orgs/{org}/workflow-catalogue"]["post"]["operationId"] == "shareWorkflow"
    adopt = paths["/projects/{id}/workflow-adoptions"]["post"]
    assert adopt["operationId"] == "adoptWorkflow"
    assert {"201", "409", "422"} <= set(adopt["responses"])


def test_an_entry_says_where_each_agent_resolves() -> None:
    schemas = contracts.load_openapi()["components"]["schemas"]
    entry = schemas["WorkflowCatalogueEntry"]
    assert {"source", "requires", "needs_repository", "agents", "groups"} <= set(entry["required"])
    resolution = schemas["WorkflowCatalogueAgent"]["properties"]["resolution"]["enum"]
    assert set(resolution) == {"org", "plugin", "remote", "catalogue", "unresolved"}
    assert "catalogue" in schemas["WorkflowDef"]["properties"]["source"]["enum"]
    assert "origin" in schemas["WorkflowDef"]["properties"]


def test_metadata_catalogue_is_validated_and_optional() -> None:
    import yaml

    document = yaml.safe_load(template_yaml("default-request"))
    Workflow.model_validate(document)
    document["metadata"]["catalogue"] = {"title": "A request", "tags": ["general", "no-repository"]}
    assert Workflow.model_validate(document).metadata.catalogue is not None
    document["metadata"]["catalogue"] = {"tags": ["Not A Slug"]}
    with pytest.raises(ValidationError):
        Workflow.model_validate(document)
    document["metadata"]["catalogue"] = {"colour": "red"}
    with pytest.raises(ValidationError):
        Workflow.model_validate(document)
