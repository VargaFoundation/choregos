"""A template ships the knowledge bases its agents read (S51-06, ADR 0117): `defaults.knowledge`.

A project born of the template gets each base, owned by it and served to it, its git sources attached
and their sync loops started; the template applied again duplicates nothing; a base of that slug the
organisation already has is reused when it is served, served when the template's own material allows it,
and left alone otherwise, with the reason in the audit log.
"""

from __future__ import annotations

import pathlib
import time
from typing import Any

import pytest
import yaml
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"
KB = f"{ORG}/knowledge"
REPO = "https://github.com/acme/billing.git"

MANIFEST: dict[str, Any] = {
    "apiVersion": "choregos/v1",
    "kind": "Template",
    "metadata": {"name": "kb-flow", "version": "1.0.0", "display": "KB flow"},
    "requires": {"connectors": {}},
    "defaults": {
        "workflows": ["template:default-simple@1"],
        "knowledge": [
            {
                "slug": "product-docs",
                "name": "Product documentation",
                "serving_mode": "index",
                "sources": [
                    {
                        "name": "Repository docs",
                        "kind": "git",
                        "config": {"url_from_input": "repo", "paths": ["docs/**/*.md"]},
                        "interval_minutes": 30,
                    }
                ],
            },
            {
                "slug": "house-style",
                "name": "House style",
                "sources": [
                    {
                        "name": "Style guide",
                        "kind": "git",
                        "config": {"url": "https://github.com/varga-foundation/style.git", "ref": "v2"},
                        "refresh_mode": "cron",
                        "refresh_cron": "0 6 * * 1-5",
                        "refresh_timezone": "Europe/Paris",
                    }
                ],
            },
        ],
    },
    "inputs": [{"name": "repo", "type": "github-repo", "required": True}],
    "steps": [],
}


def _write(root: pathlib.Path, manifest: dict[str, Any]) -> None:
    folder = root / str(manifest["metadata"]["name"])
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "manifest.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


@pytest.fixture
def templates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    root = tmp_path / "templates"
    _write(root, MANIFEST)
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(root))
    return root


async def _born(
    client: AsyncClient, slug: str = "billing", repo: str | None = REPO, inputs: dict[str, Any] | None = None
) -> Any:
    config: dict[str, Any] = {"slug": slug, "org": "varga"}
    if repo:
        config["repo"] = {"url": repo, "default_branch": "main", "language": "python"}
    body: dict[str, Any] = {"slug": slug, "name": slug, "template_ref": "kb-flow", "config": config}
    if inputs is not None:
        body["inputs"] = inputs
    return await client.post(f"{ORG}/projects", json=body)


async def _audit(client: AsyncClient, action: str) -> list[dict[str, Any]]:
    return [a for a in (await client.get("/api/v1/audit")).json()["items"] if a["action"] == action]


async def _base(client: AsyncClient, slug: str) -> dict[str, Any]:
    found = await client.get(f"{KB}/{slug}")
    assert found.status_code == 200, found.text
    return dict(found.json())


def _loops() -> dict[str, Any]:
    from choregos_api.temporal import get_temporal

    return {k: v for k, v in get_temporal().started.items() if k.startswith("knowledge-sync-")}  # type: ignore[attr-defined]


def test_the_template_schema_accepts_defaults_knowledge_and_refuses_what_it_cannot_install() -> None:
    import choregos_contracts as contracts
    from jsonschema import Draft202012Validator

    schema = Draft202012Validator(contracts.load_schema("template.schema.json"))
    assert not list(schema.iter_errors(MANIFEST))

    def with_source(**source: Any) -> dict[str, Any]:
        base = {"slug": "docs", "name": "Docs", "sources": [{"name": "s", "kind": "git", **source}]}
        return {**MANIFEST, "defaults": {**MANIFEST["defaults"], "knowledge": [base]}}

    both = with_source(config={"url": "https://github.com/a/b", "url_from_input": "repo"})
    neither = with_source(config={"ref": "main"})
    plain_http = with_source(config={"url": "http://github.com/a/b"})
    web = with_source(kind="web", config={"url": "https://example.com"})
    for refused in (both, neither, plain_http, web):
        assert list(schema.iter_errors(refused)), refused["defaults"]["knowledge"]
    unknown_key = {
        **MANIFEST,
        "defaults": {**MANIFEST["defaults"], "knowledge": [{"slug": "x1", "name": "X", "scope": "org"}]},
    }
    assert list(schema.iter_errors(unknown_key)), "the project decides the scope, not the template"


async def test_a_project_born_of_the_template_gets_its_bases_served_owned_and_syncing(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    born = await _born(client)
    assert born.status_code == 201, born.text

    docs = await _base(client, "product-docs")
    assert (docs["scope"], docs["projects"], docs["owner_project"]) == ("projects", ["billing"], "billing")
    assert (docs["serving_mode"], docs["created_by"]) == ("index", "template:kb-flow")
    (source,) = docs["sources"]
    assert source["kind"] == "git" and source["connector"] is None and source["interval_minutes"] == 30
    # The input left empty is the project's repository, read through the deployment's GitHub App.
    assert source["config"] == {"url": REPO, "ref": "main", "paths": ["docs/**/*.md"], "github_app": True}

    style = await _base(client, "house-style")
    (guide,) = style["sources"]
    assert guide["config"] == {
        "url": "https://github.com/varga-foundation/style.git",
        "ref": "v2",
        "paths": ["*.md"],
    }
    assert (guide["refresh_mode"], guide["refresh_cron"], guide["refresh_timezone"]) == (
        "cron", "0 6 * * 1-5", "Europe/Paris",
    )  # fmt: skip

    # Each source's loop started once the project was committed: its first pass syncs.
    assert _loops() == {
        f"knowledge-sync-{source['id']}": {
            "org": "varga",
            "source_id": source["id"],
            "interval_seconds": 1800,
        },
        f"knowledge-sync-{guide['id']}": {"org": "varga", "source_id": guide["id"], "interval_seconds": 3600},
    }
    assert len(await _audit(client, "knowledge.create")) == 2
    assert {a["payload"]["via"] for a in await _audit(client, "knowledge.source.create")} == {
        "template:kb-flow"
    }

    # The first pass brings the repository's documents into the base.
    from choregos_adapters.fakes.documents import fake_source
    from choregos_api.knowledge.sync import sync_source

    fake_source(source["id"]).put("docs/setup.md", "docs-setup.md", b"# Setup\n\nRun make.\n")
    await sync_source("varga", source["id"])
    assert [d["filename"] for d in (await _base(client, "product-docs"))["documents"]] == ["docs-setup.md"]


async def test_applying_the_template_again_duplicates_nothing(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    from choregos_api.db.models import KnowledgeBase, KnowledgeSource, Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.gabarits import livraison, manifeste_du_gabarit
    from choregos_api.services.installation import installer_ce_que_livre_le_gabarit
    from sqlalchemy import func, select

    born = await _born(client)
    assert born.status_code == 201, born.text
    loops = _loops()
    for _ in range(2):
        async with session_scope(orgs="*") as session:
            project = await session.get(Project, born.json()["id"])
            assert project is not None
            delivered = livraison(*await manifeste_du_gabarit(session, "kb-flow", project.org_id))
            done = await installer_ce_que_livre_le_gabarit(session, project, delivered, "kb-flow")
            assert sorted(done["knowledge"]) == ["house-style", "product-docs"]
    async with session_scope(orgs="*") as session:
        assert (await session.execute(select(func.count()).select_from(KnowledgeBase))).scalar_one() == 2
        assert (await session.execute(select(func.count()).select_from(KnowledgeSource))).scalar_one() == 2
    assert _loops() == loops, "no new loop: no new source"
    assert len(await _audit(client, "knowledge.source.create")) == 2


async def test_a_source_the_project_lost_is_added_back_and_only_it(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    from choregos_api.db.models import Project
    from choregos_api.db.session import session_scope
    from choregos_api.services.gabarits import livraison, manifeste_du_gabarit
    from choregos_api.services.installation import installer_ce_que_livre_le_gabarit

    born = await _born(client)
    (gone,) = (await _base(client, "house-style"))["sources"]
    assert (await client.delete(f"{KB}/house-style/sources/{gone['id']}")).status_code == 204
    async with session_scope(orgs="*") as session:
        project = await session.get(Project, born.json()["id"])
        assert project is not None
        delivered = livraison(*await manifeste_du_gabarit(session, "kb-flow", project.org_id))
        await installer_ce_que_livre_le_gabarit(session, project, delivered, "kb-flow")
    (back,) = (await _base(client, "house-style"))["sources"]
    assert back["name"] == "Style guide" and back["id"] != gone["id"]
    assert len((await _base(client, "product-docs"))["sources"]) == 1
    assert f"knowledge-sync-{back['id']}" in _loops()


async def test_a_second_project_is_served_the_templates_own_material_but_not_the_first_ones_repository(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    assert (await _born(client)).status_code == 201
    second = await _born(client, "billing-2", "https://github.com/acme/other.git")
    assert second.status_code == 201, second.text

    assert (await _base(client, "house-style"))["projects"] == ["billing", "billing-2"]
    assert len((await _base(client, "house-style"))["sources"]) == 1, "the template's material, once"
    docs = await _base(client, "product-docs")
    assert docs["projects"] == ["billing"], "billing's repository is not shown to billing-2"
    (skip,) = await _audit(client, "template.knowledge.skip")
    assert skip["target_id"] == second.json()["id"]
    assert skip["payload"]["knowledge"] == "product-docs"
    assert "reads the repository of project `billing`" in skip["payload"]["reason"]


async def test_a_base_the_organisation_serves_is_reused_untouched(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    created = await client.post(KB, json={"slug": "product-docs", "name": "Ours", "scope": "org"})
    assert created.status_code == 201, created.text
    assert (await _born(client)).status_code == 201
    docs = await _base(client, "product-docs")
    assert (docs["name"], docs["scope"], docs["sources"], docs["owner_project"]) == ("Ours", "org", [], None)
    assert await _audit(client, "template.knowledge.skip") == []


async def test_a_base_of_that_slug_not_served_to_the_project_is_left_alone_and_said(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    created = await client.post(
        KB, json={"slug": "product-docs", "name": "Theirs", "scope": "groups", "groups": ["finance"]}
    )
    assert created.status_code == 201, created.text
    born = await _born(client)
    assert born.status_code == 201, born.text
    docs = await _base(client, "product-docs")
    assert (docs["scope"], docs["groups"], docs["sources"]) == ("groups", ["finance"], [])
    (skip,) = await _audit(client, "template.knowledge.skip")
    assert skip["payload"]["knowledge"] == "product-docs"
    assert "is not served to this project" in skip["payload"]["reason"]


async def test_an_input_names_another_repository_read_without_the_app(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    born = await _born(client, inputs={"repo": "acme/handbook"})
    assert born.status_code == 201, born.text
    (source,) = (await _base(client, "product-docs"))["sources"]
    assert source["config"] == {
        "url": "https://github.com/acme/handbook",
        "ref": "main",
        "paths": ["docs/**/*.md"],
    }


async def test_without_a_repository_the_source_is_skipped_and_said(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    born = await _born(client, repo=None)
    assert born.status_code == 201, born.text
    assert (await _base(client, "product-docs"))["sources"] == []
    (skip,) = await _audit(client, "template.knowledge.skip")
    assert "source `Repository docs`: the input `repo` has no value" in skip["payload"]["reason"]


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        (lambda m: m["defaults"]["knowledge"][0]["sources"][0]["config"].update(url_from_input="nope"),
         "reads the input `nope`, which the template does not declare"),
        (lambda m: m["defaults"]["knowledge"].append(dict(m["defaults"]["knowledge"][1])),
         "ships a knowledge base twice"),
        (lambda m: m["defaults"]["knowledge"][1]["sources"][0].update(refresh_cron="* * * * *"),
         "the minimum is 5"),
    ],
)  # fmt: skip
async def test_a_template_whose_knowledge_is_wrong_creates_no_project(
    client: AsyncClient, admin: str, templates: pathlib.Path, change: Any, needle: str
) -> None:
    import copy

    manifest = copy.deepcopy(MANIFEST)
    change(manifest)
    _write(templates, manifest)
    refused = await _born(client)
    assert refused.status_code == 422, refused.text
    assert needle in refused.text
    assert "billing" not in {p["slug"] for p in (await client.get(f"{ORG}/projects")).json()["items"]}


# ── the project's own repository, read through the deployment's GitHub App ──


@pytest.fixture
def minted(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from choregos_adapters.github import GitHubAppAuth, InstallationToken
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_GITHUB_APP_ID", "5242171")
    monkeypatch.setenv(
        "CHOREGOS_GITHUB_APP_PRIVATE_KEY", "-----BEGIN RSA PRIVATE KEY-----\nx\n-----END RSA PRIVATE KEY-----"
    )
    reset_settings_cache()
    asked: list[dict[str, Any]] = []

    async def mint(self: Any, client: Any, repo: str, **options: Any) -> InstallationToken:
        asked.append({"repo": repo, "pinned": self.pinned_installation, **options})
        return InstallationToken(token="ghs_kb", expires_at=time.time() + 3600, repositories=(repo,))

    monkeypatch.setattr(GitHubAppAuth, "token_for", mint)
    return asked


async def _source_row(client: AsyncClient) -> Any:
    from choregos_api.db.models import KnowledgeSource
    from choregos_api.db.session import session_scope

    (source,) = (await _base(client, "product-docs"))["sources"]
    async with session_scope(orgs="*") as session:
        row = await session.get(KnowledgeSource, source["id"])
        session.expunge(row)
    return row


async def test_the_projects_repository_is_read_with_a_read_only_token_of_the_app(
    client: AsyncClient, admin: str, templates: pathlib.Path, minted: list[dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    from choregos_adapters.documents.git import GitSource
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge import sync

    assert (await _born(client)).status_code == 201
    row = await _source_row(client)
    monkeypatch.setattr(sync, "fakes_enabled", lambda: False)
    async with session_scope(orgs="*") as session:
        opened = await sync.open_source(session, row)
    assert isinstance(opened, GitSource)
    assert (opened.url, opened.host) == (REPO, "github.com")
    assert await opened._token() == "ghs_kb"
    assert minted == [{"repo": "acme/billing", "pinned": None, "permissions": {"contents": "read"}}]


async def test_without_an_app_the_projects_repository_cannot_be_opened(
    client: AsyncClient, admin: str, templates: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.db.session import session_scope
    from choregos_api.knowledge import sync

    monkeypatch.delenv("CHOREGOS_GITHUB_APP_ID", raising=False)
    reset_settings_cache()
    assert (await _born(client)).status_code == 201
    row = await _source_row(client)
    monkeypatch.setattr(sync, "fakes_enabled", lambda: False)
    async with session_scope(orgs="*") as session:
        with pytest.raises(sync.SourceUnavailableError, match="no GitHub App"):
            await sync.open_source(session, row)


async def test_no_request_grants_the_app_and_an_edit_keeps_it_only_on_the_same_repository(
    client: AsyncClient, admin: str, templates: pathlib.Path
) -> None:
    assert (await _born(client)).status_code == 201
    (source,) = (await _base(client, "product-docs"))["sources"]
    path = f"{KB}/product-docs/sources/{source['id']}"

    same = {"url": REPO, "ref": "dev", "paths": ["*.md"]}
    edited = await client.patch(path, json={"config": same, "allow_egress": True})
    assert edited.status_code == 200, edited.text
    assert edited.json()["config"] == {**same, "github_app": True}

    asked = {"url": "https://github.com/acme/secrets", "github_app": True}
    elsewhere = await client.patch(path, json={"config": asked, "allow_egress": True})
    assert elsewhere.status_code == 200, elsewhere.text
    assert "github_app" not in elsewhere.json()["config"]

    created = await client.post(
        f"{KB}/product-docs/sources",
        json={"kind": "git", "name": "asked", "config": asked, "allow_egress": True},
    )
    assert created.status_code == 201, created.text
    assert "github_app" not in created.json()["config"]
