"""`tools/rename_tenant_names.py` (S39-11c): a tenant born from the 1.x `joiners-leavers` template is
re-published in English, through the public API only.

The tenant is seeded from `fixtures/joiners-leavers-v1/` (the French template, kept as it was). The
script is played in process against the API test app, as an organisation administrator.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
from typing import Any

import pytest
import yaml
from httpx import AsyncClient

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "joiners-leavers-v1"
ORG = "/api/v1/orgs/varga"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "rename_tenant_names", ROOT / "tools" / "rename_tenant_names.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tool = _load()
MAPPING = yaml.safe_load((ROOT / "tools" / "rename_tenant_names.yaml").read_text(encoding="utf-8"))


@pytest.fixture
async def tenant(client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """A tenant as it was before S39-11: the French template's project, agents, skills, connectors."""
    import choregos_adapters
    from choregos_adapters.fakes.rh import FakeFournisseur, serveur_mcp

    monkeypatch.setattr(choregos_adapters, "FAUX_MCP", serveur_mcp(FakeFournisseur()))
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(FIXTURES))
    monkeypatch.setenv("RENAME_TEST_SECRET", "s3cret")
    for body in (
        {
            "name": "annuaire",
            "kind": "identity",
            "type": "entra",
            "config": {"tenant_id": "acme", "client_id": "choregos"},
            "secret_refs": {"client_secret": "env:RENAME_TEST_SECRET"},
        },
        {"name": "parc", "kind": "mdm", "type": "demo"},
        {"name": "transporteur", "kind": "shipping", "type": "demo"},
        {"name": "lecteurs", "kind": "access_control", "type": "demo"},
        {"name": "fournisseur", "kind": "mcp", "type": "mcp", "config": {"url": "https://supplier.test/mcp"}},
    ):
        assert (await client.post(f"{ORG}/connectors", json=body)).status_code == 201
    assert (await client.post(f"{ORG}/connectors/fournisseur/discover")).status_code == 200
    # What the organisation decided: a read opened to some groups, a purchase under approval.
    for path, patch in (
        ("annuaire/operations/get_user", {"policy": "allowed", "groups": ["it"]}),
        ("parc/operations/wipe_device", {"policy": "allowed"}),
        ("fournisseur/operations/order_laptop", {"policy": "approval"}),
    ):
        done = await client.patch(f"{ORG}/connectors/{path}", json=patch)
        assert done.status_code == 200, done.text
    created = await client.post(
        f"{ORG}/projects",
        json={
            "slug": "rh",
            "name": "rh",
            "template_ref": "joiners-leavers",
            "config": {"slug": "rh", "org": "varga"},
        },
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    assert (
        await client.put(
            f"/api/v1/projects/{pid}/connectors/tracker", json={"type": "internal", "config": {}}
        )
    ).status_code == 200
    narrowed = await client.put(
        f"/api/v1/projects/{pid}/operations/parc/wipe_device", json={"policy": "forbidden"}
    )
    assert narrowed.status_code == 200, narrowed.text
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(ROOT / "templates"))
    return tool.Tenant(client, "varga", "https://console.test"), pid


def _kinds(report: Any) -> list[str]:
    return [f"{s.kind}:{s.target}" for s in report.steps]


async def test_dry_run_lists_the_plan_and_writes_nothing(client: AsyncClient, tenant: Any) -> None:
    api, pid = tenant
    report = await tool.plan(api, MAPPING)
    steps = _kinds(report)
    assert "skill:onboarding-procedure" in steps and "agent:onboarding-coordinator" in steps
    assert {"connector:directory", "connector:devices", "connector:supplier"} <= set(steps)
    assert "workflow:rh/onboarding" in steps and "workflow:rh/offboarding" in steps
    assert "project:rh" in steps and "agent:coordinateur-onboarding" in steps  # lang: fr-ok
    assert "nothing" not in tool.render(report)
    # Nothing was written.
    assert (await client.get(f"{ORG}/connectors/directory")).status_code == 404
    assert (await client.get(f"{ORG}/skills/onboarding-procedure")).status_code == 404
    assert (await client.get(f"/api/v1/projects/{pid}/workflows/onboarding")).json()["version"] == 1


async def test_apply_republishes_in_english_and_a_second_run_finds_nothing(
    client: AsyncClient, tenant: Any
) -> None:
    api, pid = tenant
    await tool.apply(await tool.plan(api, MAPPING))

    assert (await client.get(f"{ORG}/skills/access-profiles")).status_code == 200
    agents = {a["slug"]: a for a in (await client.get(f"{ORG}/agents")).json()}
    assert agents["onboarding-coordinator"]["lifecycle"] == "active"
    assert agents["coordinateur-onboarding"]["lifecycle"] == "deprecated"  # lang: fr-ok

    directory = (await client.get(f"{ORG}/connectors/directory")).json()
    assert (directory["kind"], directory["type"]) == ("identity", "entra")
    ops = {o["name"]: o for o in directory["operations"]}
    assert (ops["get_user"]["policy"], ops["get_user"]["groups"]) == ("allowed", ["it"])
    assert directory["secret_refs"] == {"client_secret": "env:RENAME_TEST_SECRET"}, "references, verbatim"
    devices = {
        o["name"]: o["policy"] for o in (await client.get(f"{ORG}/connectors/devices")).json()["operations"]
    }
    assert devices["wipe_device"] == "allowed"
    supplier = {
        o["name"]: o["policy"] for o in (await client.get(f"{ORG}/connectors/supplier")).json()["operations"]
    }
    assert supplier["order_laptop"] == "approval"
    tight = {
        (o["connector"], o["operation"]): o["project_policy"]
        for o in (await client.get(f"/api/v1/projects/{pid}/operations")).json()
    }
    assert tight[("devices", "wipe_device")] == "forbidden", "the project's tightening followed"

    onboarding = (await client.get(f"/api/v1/projects/{pid}/workflows/onboarding")).json()
    assert onboarding["version"] == 2 and "requested" in onboarding["json"]["states"]
    routing = (await client.get(f"/api/v1/projects/{pid}/workflow-routing")).json()
    assert routing["rules"][0]["when"]["labels_any"] == ["offboarding", "leaver"]
    assert (await client.get(f"/api/v1/projects/{pid}")).json()["name"] == "HR"

    again = await tool.plan(api, MAPPING)
    assert again.steps == [], _kinds(again)
    # The old things are still there: nothing was deleted.
    assert (await client.get(f"{ORG}/connectors/annuaire")).status_code == 200  # lang: fr-ok


async def test_open_work_items_stay_on_their_version_and_block_the_workflow_without_the_flag(
    client: AsyncClient, tenant: Any
) -> None:
    api, pid = tenant
    item = await client.post(
        f"/api/v1/projects/{pid}/work-items",
        json={
            "title": "Joiner",
            "start": False,
            "fields": {
                "nom": "Lea Martin",  # lang: fr-ok
                "upn": "lea@acme.test",
                "date_arrivee": "2026-12-01",  # lang: fr-ok
                "poste": "Dev",  # lang: fr-ok
                "groupe": "devs",  # lang: fr-ok
                "groupe_sensible": "prod",  # lang: fr-ok
                "adresse": "1 Main Street",  # lang: fr-ok
            },
        },
    )
    assert item.status_code == 201, item.text
    before = item.json()

    blocked = await tool.plan(api, MAPPING)
    assert "workflow:rh/onboarding" not in _kinds(blocked)
    assert any("rh/onboarding" in s and "--allow-open" in s for s in blocked.skipped)
    assert any(before["tracker_key"] in line and "/p/rh/items/" in line for line in blocked.open_items)

    await tool.apply(await tool.plan(api, MAPPING, allow_open=True))
    after = (await client.get(f"/api/v1/work-items/{before['id']}")).json()
    assert (after["workflow_version"], after["state"]) == (before["workflow_version"], before["state"])
    new_version = (await client.get(f"/api/v1/projects/{pid}/workflows/onboarding")).json()["version"]
    assert new_version == before["workflow_version"] + 1


async def test_hand_made_labels_are_translated_and_state_ids_are_untouched(
    client: AsyncClient, tenant: Any
) -> None:
    api, _ = tenant
    created = await client.post(
        f"{ORG}/projects",
        json={
            "slug": "preuve",
            "name": "Preuve",
            "config": {"slug": "preuve", "org": "varga"},
        },  # lang: fr-ok
    )
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    hand_made = """\
apiVersion: choregos/v1
kind: Workflow
metadata: {name: preuve-agent, version: 1}
initial: inbox
actors: {boss: {type: human, group: it}}
states:
  inbox: {display: "À faire", kind: wait}
  en_cours: {display: "En cours", kind: wait}
  fini: {display: "Fini", terminal: true}
transitions:
  - {id: t-go, from: inbox, to: en_cours, by: boss}
  - {id: t-end, from: en_cours, to: fini, by: boss}
"""  # lang: fr-ok
    published = await client.put(f"/api/v1/projects/{pid}/workflows/preuve-agent", json={"yaml": hand_made})
    assert published.status_code == 200, published.text
    report = await tool.plan(api, MAPPING)
    assert "workflow:preuve/preuve-agent" in _kinds(report)
    await tool.apply(report)
    done = (await client.get(f"/api/v1/projects/{pid}/workflows/preuve-agent")).json()
    states = done["json"]["states"]
    assert set(states) == {"inbox", "en_cours", "fini"}, "ids untouched"  # lang: fr-ok
    assert [s["display"] for s in states.values()] == ["To do", "In progress", "Done"]
    assert done["version"] == 2
    assert "workflow:preuve/preuve-agent" not in _kinds(await tool.plan(api, MAPPING))
