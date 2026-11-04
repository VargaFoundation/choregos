"""What a template delivers to an extension (S20-09): `defaults.extensions: {<installer>: <folder>}`.

When the project is born, the core reads the folder (never leaving it, no symbolic link) and
hands it to the installer declared under that name, in the transaction that creates the project:
a refusal by the installer makes the birth fail. Without an installer, the project is born
without, and the audit says so.
"""

from __future__ import annotations

import pathlib
from typing import Any

import pytest
from httpx import AsyncClient

ORG = "/api/v1/orgs/varga"


@pytest.fixture
def templates(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """A separate templates folder, and a template `trial-ext` that delivers the extension `trial`."""
    root = tmp_path / "templates"
    folder = root / "trial-ext"
    (folder / "trial" / "sub").mkdir(parents=True)
    (folder / "manifest.yaml").write_text(
        "apiVersion: choregos/v1\nkind: Template\nmetadata: {name: trial-ext, version: 1.0.0}\n"
        "defaults:\n  workflows: ['template:default-simple@1']\n  extensions: {trial: ./trial}\n",
        encoding="utf-8",
    )
    (folder / "trial" / "a.yaml").write_text("a: 1\n", encoding="utf-8")
    (folder / "trial" / "sub" / "b.yaml").write_text("b: 2\n", encoding="utf-8")
    monkeypatch.setenv("CHOREGOS_TEMPLATES_DIR", str(root))
    return folder


@pytest.fixture
def installers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The registry of installers, private to the test."""
    from choregos_api import extensions

    registry: dict[str, Any] = {}
    monkeypatch.setattr(extensions, "_INSTALLERS", registry)
    return registry


async def _create(client: AsyncClient, slug: str = "hr") -> Any:
    return await client.post(
        f"{ORG}/projects",
        json={
            "slug": slug,
            "name": slug,
            "template_ref": "trial-ext",
            "config": {"slug": slug, "org": "varga"},
        },
    )


async def _audit(client: AsyncClient, action: str) -> list[dict[str, Any]]:
    return [a for a in (await client.get("/api/v1/audit")).json()["items"] if a["action"] == action]


async def test_the_declared_installer_receives_the_folder_and_the_audit_says_so(
    client: AsyncClient, admin: str, templates: pathlib.Path, installers: dict[str, Any]
) -> None:
    from choregos_api.extensions import declare_template_installer

    received: list[tuple[str, dict[str, str], str]] = []

    async def installer(session: Any, project: Any, files: dict[str, str], author: str) -> dict[str, Any]:
        received.append((project.id, files, author))
        return {"installed": sorted(files)}

    declare_template_installer("trial", installer)
    created = await _create(client)
    assert created.status_code == 201, created.text
    assert received == [
        (created.json()["id"], {"a.yaml": "a: 1\n", "sub/b.yaml": "b: 2\n"}, "template:trial-ext")
    ]
    (line,) = await _audit(client, "template.extension.install")
    assert line["target_id"] == created.json()["id"]
    assert line["payload"]["extension"] == "trial"
    assert line["payload"]["result"] == {"installed": ["a.yaml", "sub/b.yaml"]}
    assert await _audit(client, "template.extension.skip") == []


async def test_without_an_installer_the_project_is_born_without_and_the_audit_says_so(
    client: AsyncClient, admin: str, templates: pathlib.Path, installers: dict[str, Any]
) -> None:
    created = await _create(client)
    assert created.status_code == 201, created.text
    (line,) = await _audit(client, "template.extension.skip")
    assert line["target_id"] == created.json()["id"]
    assert line["payload"]["extension"] == "trial"
    assert "no active extension" in line["payload"]["reason"]


async def test_a_refusal_by_the_installer_makes_the_birth_fail(
    client: AsyncClient, admin: str, templates: pathlib.Path, installers: dict[str, Any]
) -> None:
    from choregos_api.errors import unprocessable
    from choregos_api.extensions import declare_template_installer

    async def installer(session: Any, project: Any, files: dict[str, str], author: str) -> dict[str, Any]:
        raise unprocessable("wrong package")

    declare_template_installer("trial", installer)
    refusal = await _create(client)
    assert refusal.status_code == 422, refusal.text
    assert "wrong package" in refusal.text
    assert "hr" not in {p["slug"] for p in (await client.get(f"{ORG}/projects")).json()["items"]}


@pytest.mark.parametrize("case", ["outside the template", "symbolic link"])
async def test_a_folder_that_leaves_the_template_is_refused(
    client: AsyncClient, admin: str, templates: pathlib.Path, installers: dict[str, Any], case: str
) -> None:
    from choregos_api.extensions import declare_template_installer

    received: list[str] = []

    async def installer(session: Any, project: Any, files: dict[str, str], author: str) -> dict[str, Any]:
        received.append(project.id)
        return {}

    declare_template_installer("trial", installer)
    secret = templates.parent.parent / "secret.yaml"
    secret.write_text("key: value\n", encoding="utf-8")
    manifest = templates / "manifest.yaml"
    if case == "outside the template":
        manifest.write_text(manifest.read_text().replace("./trial", "../../"), encoding="utf-8")
    else:
        (templates / "trial" / "link.yaml").symlink_to(secret)
    refusal = await _create(client)
    assert refusal.status_code == 422, refusal.text
    assert received == []


def test_two_extensions_do_not_declare_the_same_installer(installers: dict[str, Any]) -> None:
    from choregos_api.extensions import (
        declare_template_installer,
        reset,
        template_installers,
    )

    async def one(*_: Any) -> dict[str, Any]:
        return {}

    async def other(*_: Any) -> dict[str, Any]:
        return {}

    declare_template_installer("trial", one)
    declare_template_installer("trial", one)
    with pytest.raises(ValueError, match="déjà déclaré"):  # lang: fr-ok (the source message is still French)
        declare_template_installer("trial", other)
    reset()
    assert template_installers() == {}
