"""A template delivers several workflows, their default, their routing and its policy (ADR 0031, S16-07).

`ensure_defaults` ignored the manifest: whatever its template, a project was born with
`default-simple` and `solo`. An HR template could therefore not deliver the arrival AND the departure.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
import yaml
from httpx import AsyncClient


def _flow(name: str, state: str) -> str:
    return f"""apiVersion: choregos/v1
kind: Workflow
metadata: {{name: {name}, version: 1}}
actors:
  hr: {{type: human, group: hr}}
states:
  {state}: {{display: Request, kind: wait}}
  done: {{display: Done, terminal: true}}
transitions:
  - {{id: t-do, from: {state}, to: done, by: hr}}
"""


@pytest.fixture
def templates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A templates folder of its own; `write(defaults)` puts the `hr` template in it."""
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(tmp_path))
    (tmp_path / "secret.yaml").write_text(_flow("leak", "outside"), encoding="utf-8")

    def write(defaults: dict[str, Any]) -> None:
        folder = tmp_path / "hr"
        (folder / "workflows").mkdir(parents=True, exist_ok=True)
        (folder / "workflows" / "arrival.yaml").write_text(_flow("onboarding", "arrival"), encoding="utf-8")
        (folder / "workflows" / "departure.yaml").write_text(
            _flow("offboarding", "departure"), encoding="utf-8"
        )
        manifest = {
            "apiVersion": "choregos/v1",
            "kind": "Template",
            "metadata": {"name": "hr", "version": "1.0.0", "display": "HR"},
            "requires": {"connectors": {"tracker": "internal"}},
            "defaults": defaults,
            "inputs": [],
            "steps": [],
        }
        (folder / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")

    return write


async def _project(client: AsyncClient, template_ref: str, slug: str = "hr") -> Any:
    return await client.post(
        "/api/v1/orgs/varga/projects",
        json={
            "slug": slug,
            "name": slug,
            "template_ref": template_ref,
            "config": {"slug": slug, "org": "varga"},
        },
    )


ARRIVAL_AND_DEPARTURE = {
    "workflows": ["workflows/arrival.yaml", "workflows/departure.yaml"],
    "default_workflow": "onboarding",
    "routing": [{"when": {"labels_any": ["leaver"]}, "workflow": "offboarding"}],
    "policy": "preset:team",
}


async def test_a_project_born_from_a_two_workflow_template_receives_them(
    client: AsyncClient, admin: str, templates: Any
) -> None:
    templates(ARRIVAL_AND_DEPARTURE)
    created = await _project(client, "hr@1.0.0")
    assert created.status_code == 201, created.text
    pid = created.json()["id"]

    active = {w["name"] for w in (await client.get(f"/api/v1/projects/{pid}/workflows")).json()}
    assert active == {"onboarding", "offboarding"}
    routing = (await client.get(f"/api/v1/projects/{pid}/workflow-routing")).json()
    assert routing["default"] == "onboarding"
    assert routing["rules"] == [
        {"when": {"labels_any": ["leaver"], "labels_all": [], "item_type": None}, "workflow": "offboarding"}
    ]
    assert (await client.get(f"/api/v1/projects/{pid}/policy")).json()["name"] == "team"

    # The delivered routing is used: a request labelled `leaver` is born at the departure.
    tracker = await client.put(
        f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "internal", "config": {}}
    )
    assert tracker.status_code == 200, tracker.text
    for labels, state in (([], "arrival"), (["leaver"], "departure")):
        item = await client.post(
            f"/api/v1/projects/{pid}/work-items",
            json={"title": "Camille", "labels": labels, "start": False},
        )
        assert item.status_code == 201, item.text
        assert item.json()["state"] == state


@pytest.mark.parametrize(
    ("defaults", "reason"),
    [
        pytest.param({"workflows": ["../secret.yaml"]}, "goes outside", id="outside-the-folder"),
        pytest.param({"workflows": ["workflows/absent.yaml"]}, "missing", id="missing-file"),
        pytest.param(
            {"workflows": ["workflows/arrival.yaml"], "routing": [{"when": {}, "workflow": "offboarding"}]},
            "offboarding",
            id="routing-to-an-undelivered-workflow",
        ),
        pytest.param({"workflows": ["template:unknown@1"]}, "unknown", id="unknown-core-template"),
        pytest.param(
            {"workflows": ["workflows/arrival.yaml"], "policy": "/etc/passwd"},
            "goes outside",
            id="policy",
        ),
        pytest.param(
            {"workflows": ["workflows/arrival.yaml"], "policy": "./missing.yaml"},
            "missing",
            id="missing-policy",
        ),
        pytest.param(
            {"workflows": ["workflows/arrival.yaml"], "policy": "preset:../solo"},
            "preset",
            id="preset-as-a-path",
        ),
    ],
)
async def test_a_faulty_template_gives_no_project(
    client: AsyncClient, admin: str, templates: Any, defaults: dict[str, Any], reason: str
) -> None:
    templates(defaults)
    refused = await _project(client, "hr@1.0.0")
    assert refused.status_code == 422, refused.text
    assert reason in refused.text
    projects = (await client.get("/api/v1/orgs/varga/projects")).json()["items"]
    assert "hr" not in {p["slug"] for p in projects}, "nothing is half-created"


async def test_the_shipped_templates_keep_their_shape(client: AsyncClient, admin: str) -> None:
    """`defaults.workflow`, singular: the shape of the templates shipped so far."""
    created = await _project(client, "github-aca@1.0.0", slug="on-azure")
    assert created.status_code == 201, created.text
    active = (await client.get(f"/api/v1/projects/{created.json()['id']}/workflows")).json()
    assert [w["name"] for w in active] == ["default-simple"]


def test_the_template_schema_knows_these_fields() -> None:
    """An administrator publishes a template in the database (`POST /templates`): the schema accepts it."""
    import choregos_contracts as contracts
    from jsonschema import Draft202012Validator

    schema = Draft202012Validator(contracts.load_schema("template.schema.json"))
    manifest = {
        "apiVersion": "choregos/v1",
        "kind": "Template",
        "metadata": {"name": "hr", "version": "1.0.0", "display": "HR"},
        "requires": {"connectors": {"tracker": "internal"}},
        "defaults": ARRIVAL_AND_DEPARTURE,
        "inputs": [],
        "steps": ["github.ensure_labels"],
    }
    assert not list(schema.iter_errors(manifest))
    without_target = {**ARRIVAL_AND_DEPARTURE, "routing": [{"when": {"labels_any": ["leaver"]}}]}
    assert list(schema.iter_errors({**manifest, "defaults": without_target})), (
        "a rule without a workflow is refused"
    )


async def test_a_template_delivers_its_own_policy(
    client: AsyncClient, admin: str, templates: Any, tmp_path: pathlib.Path
) -> None:
    """Its budgets and its trains, which no preset carries (S21-22): `./policy.yaml`, in its folder."""
    from choregos_core.policy import preset_yaml

    templates({"workflows": ["workflows/arrival.yaml"], "policy": "./policy.yaml"})
    own = preset_yaml("team").replace("name: team", "name: hr-delivery", 1)
    (tmp_path / "hr" / "policy.yaml").write_text(own, encoding="utf-8")
    created = await _project(client, "hr@1.0.0")
    assert created.status_code == 201, created.text
    policy = (await client.get(f"/api/v1/projects/{created.json()['id']}/policy")).json()
    assert policy["name"] == "hr-delivery"
