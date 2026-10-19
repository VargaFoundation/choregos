# SPDX-License-Identifier: Apache-2.0
"""The shape every setup procedure holds, and what the loader does with a deployment's additions.

ADR 0067.
"""

from __future__ import annotations

import importlib.metadata
import pathlib
from typing import Any

import pytest
from choregos_core import procedures as core
from choregos_core.procedures import SECTION_KEYS, Context, ProcedureError

SHIPPED = sorted(core.SHIPPED.glob("*.md"))

GOOD = """---
id: connector.demo.thing
title: Connect a thing
applies_to: {connector: demo/thing}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Thing docs", url: "https://example.com/docs"}
values: ["webhook_url:github", api_url]
---

## Before you start

Read [the docs](https://example.com/docs). Callback: {{webhook_url:github}}.

## At the vendor

Create it.

## Permissions

Least privilege.

## In Choregos

Paste it.

## Test it

Press Test.

## When it fails

- It fails: fix it.
"""


def _ctx() -> Context:
    return Context(api_url="https://api.example.test/", public_url="https://console.example.test", org="acme")


def test_the_shipped_files_are_all_valid_and_named_after_their_id() -> None:
    assert SHIPPED, "no procedure ships with the platform"
    catalogue = core.load()
    assert catalogue.problems == []
    assert {p.stem for p in SHIPPED} == set(catalogue.procedures)


@pytest.mark.parametrize("proc_id", sorted(core.load().procedures))
def test_a_procedure_has_the_six_sections_in_order(proc_id: str) -> None:
    proc = core.load().procedures[proc_id]
    assert tuple(s.key for s in proc.sections) == SECTION_KEYS
    assert proc.sections[1].title.startswith("At ")
    assert all(s.markdown.strip() for s in proc.sections)


@pytest.mark.parametrize("proc_id", sorted(core.load().procedures))
def test_every_link_is_https_and_listed_and_no_secret_shape_is_in_the_body(proc_id: str) -> None:
    proc = core.load().procedures[proc_id]
    listed = {d.url for d in proc.front.vendor_docs}
    for section in proc.sections:
        for target in core.links_in(section.markdown):
            assert target in listed, f"{proc_id}: {target} is not in vendor_docs"
            assert target.startswith("https://")
        for shape in core.SECRET_SHAPES:
            assert not shape.search(section.markdown), f"{proc_id}: secret-shaped text in {section.title}"
    for item in proc.front.values:
        name, _, argument = item.partition(":")
        core.resolve_placeholder(name, argument or None, _ctx())


@pytest.mark.parametrize("proc_id", sorted(core.load().procedures))
def test_a_procedure_renders_with_this_deployments_values(proc_id: str) -> None:
    proc = core.load().procedures[proc_id]
    sections = core.render(proc, _ctx())
    assert not any("{{" in s.markdown for s in sections)
    for value in core.values_of(proc, _ctx()).values():
        assert value.startswith("https://")


def test_a_good_procedure_parses_and_fills_its_placeholders() -> None:
    proc = core.parse(GOOD)
    assert proc.target == ("connector", "demo/thing")
    rendered = core.render(proc, _ctx())
    assert "https://api.example.test/api/v1/webhooks/github" in rendered[0].markdown
    assert core.values_of(proc, _ctx()) == {
        "webhook_url:github": "https://api.example.test/api/v1/webhooks/github",
        "api_url": "https://api.example.test",
    }


def test_the_oauth_callback_is_the_remote_credentials_route() -> None:
    assert (
        core.resolve_placeholder("oauth_callback", "remote", _ctx())
        == "https://api.example.test/api/v1/remote-credentials/oauth/callback"
    )


@pytest.mark.parametrize(
    ("mutation", "needle"),
    [
        (lambda t: t.replace("## Permissions\n\nLeast privilege.\n\n", ""), "sections must be"),
        (
            lambda t: (
                t.replace("## Permissions", "## TMP")
                .replace("## Test it", "## Permissions")
                .replace("## TMP", "## Test it")
            ),
            "sections must be",
        ),
        (lambda t: t.replace("{{webhook_url:github}}", "{{nope}}"), "unknown placeholder"),
        (lambda t: t.replace("{{webhook_url:github}}", "{{webhook_url:svn}}"), "unknown placeholder"),
        (
            lambda t: t.replace("https://example.com/docs).", "https://evil.example/x)."),
            "not an https URL listed",
        ),
        (lambda t: t.replace("Create it.", "Token " + "xo" + "xb-" + "A1b2C3d4E5f6."), "secret-shaped"),
        (lambda t: t.replace("Create it.", "Hash " + "a" * 40), "secret-shaped"),
        (lambda t: t.replace('url: "https://example.com/docs"', 'url: "http://example.com/docs"'), "https"),
        (lambda t: t.replace("audience: org_admin", "audience: nobody"), "audience"),
        (
            lambda t: t.replace(
                "applies_to: {connector: demo/thing}", "applies_to: {connector: a/b, source: web}"
            ),
            "exactly one",
        ),
        (lambda t: t.replace("## Test it\n\nPress Test.", "## Test it\n\n"), "empty section"),
        (lambda t: t.replace("---\nid:", "id:", 1), "front matter"),
    ],
)
def test_a_procedure_that_breaks_the_contract_is_refused(mutation: Any, needle: str) -> None:
    with pytest.raises(ProcedureError, match=needle):
        core.parse(mutation(GOOD))


def test_a_heading_inside_a_code_fence_is_not_a_section() -> None:
    text = GOOD.replace("Create it.", "```\n## Not a section\n```")
    assert [s.key for s in core.parse(text).sections] == list(SECTION_KEYS)


def _catalogue(*texts: str) -> core.Catalogue:
    return core.Catalogue({p.id: p for p in (core.parse(t) for t in texts)})


def test_a_preset_wins_when_asked_and_the_generic_one_otherwise() -> None:
    generic = GOOD
    preset = GOOD.replace("connector.demo.thing", "connector.demo.thing.vip").replace(
        "{connector: demo/thing}", "{connector: demo/thing, preset: vip}"
    )
    cat = _catalogue(generic, preset)
    assert cat.for_target("connector:demo/thing", preset="vip").id == "connector.demo.thing.vip"  # type: ignore[union-attr]
    assert cat.for_target("connector:demo/thing").id == "connector.demo.thing"  # type: ignore[union-attr]
    assert cat.for_target("connector:demo/thing", preset="other").id == "connector.demo.thing"  # type: ignore[union-attr]
    assert cat.for_target("connector:demo/none") is None
    assert cat.for_target("nonsense") is None


def test_site_notes_add_a_section_and_a_new_file_adds_a_procedure(tmp_path: pathlib.Path) -> None:
    core.reload()
    shipped_id = "connector.documents.sharepoint"
    (tmp_path / f"{shipped_id}.site.md").write_text(
        "## On this platform\n\nAsk the platform team. Callback {{oauth_callback:remote}}.\n",
        encoding="utf-8",
    )
    (tmp_path / "connector.demo.thing.md").write_text(GOOD, encoding="utf-8")
    cat = core.load(tmp_path)
    assert cat.problems == []
    notes = cat.procedures[shipped_id].sections[-1]
    assert (notes.key, notes.title) == ("site", "On this platform")
    assert "Ask the platform team" in notes.markdown
    assert cat.procedures["connector.demo.thing"].origin == "extra"
    # The shipped set alone is untouched: the additions are per directory.
    assert core.load().procedures[shipped_id].sections[-1].key == "fails"


def test_a_bad_file_in_the_directory_is_reported_and_skipped_never_fatal(tmp_path: pathlib.Path) -> None:
    (tmp_path / "connector.nothing.site.md").write_text("notes", encoding="utf-8")
    (tmp_path / "broken.md").write_text("no front matter", encoding="utf-8")
    (tmp_path / "connector.documents.sharepoint.md").write_text(
        GOOD.replace("connector.demo.thing", "connector.documents.sharepoint"), encoding="utf-8"
    )
    (tmp_path / "connector.tracker.jira.site.md").write_text(
        "Link [x](https://evil.example/y)", encoding="utf-8"
    )
    cat = core.load(tmp_path)
    assert len(cat.problems) == 4
    joined = " ".join(cat.problems)
    assert "not a procedure" in joined and "front matter" in joined
    assert "already exists" in joined and "not an https URL listed" in joined
    assert len(cat.procedures) == len(core.load().procedures)


def test_a_missing_directory_is_a_problem_not_a_crash(tmp_path: pathlib.Path) -> None:
    cat = core.load(tmp_path / "absent")
    assert any("not a directory" in p for p in cat.problems)


def test_a_plugin_adds_procedures_through_its_entry_point(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "connector.demo.thing.md").write_text(GOOD, encoding="utf-8")

    class _Point:
        name = "demo-plugin"
        value = "demo:PATH"

        def load(self) -> pathlib.Path:
            return tmp_path

    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda **kw: [_Point()] if kw.get("group") == core.ENTRY_POINT_GROUP else [],
    )
    core.reload()
    try:
        cat = core.load()
        assert cat.procedures["connector.demo.thing"].origin == "plugin:demo-plugin"
    finally:
        monkeypatch.undo()
        core.reload()


def test_org_and_public_url_fill_from_the_deployment() -> None:
    assert core.fill("{{org}} {{public_url}} {{api_url}}", _ctx()) == (
        "acme https://console.example.test https://api.example.test"
    )
