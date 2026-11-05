"""A new skill may not take the slug of a console page (S55-17).

A skill's page is `/skills/<slug>`, and the console's own pages there win over it: `library` is the
skills library, `catalogue` redirects to it (S55-15). A skill of that slug was published, listed, and
could never be opened.
"""

from __future__ import annotations

import io
import zipfile
from typing import Any

import pytest
from httpx import AsyncClient

from .kits.skill_library import SKILL_FILES, SKILL_MD


def _files(slug: str) -> dict[str, str]:
    return {**SKILL_FILES, "SKILL.md": SKILL_MD.replace("name: procedure-onboarding", f"name: {slug}")}


def _zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
    return buffer.getvalue()


async def _import(client: AsyncClient, files: dict[str, str]) -> Any:
    return await client.post(
        "/api/v1/orgs/varga/skills/import", content=_zip(files), headers={"Content-Type": "application/zip"}
    )


def _reserved(response: Any) -> None:
    assert response.status_code == 422, response.text
    body = response.json()
    assert "is reserved" in body["detail"]
    assert body["errors"] == [
        {"loc": ["SKILL.md", "name"], "msg": body["errors"][0]["msg"], "code": "skill.slug_reserved"}
    ]


@pytest.mark.parametrize("slug", ["library", "catalogue"])
async def test_a_new_skill_named_after_a_console_page_is_refused(
    client: AsyncClient, admin: str, slug: str
) -> None:
    _reserved(await client.post("/api/v1/orgs/varga/skills", json={"files": _files(slug)}))
    _reserved(await _import(client, _files(slug)))
    listed = {s["slug"] for s in (await client.get("/api/v1/orgs/varga/skills")).json()}
    assert slug not in listed, "nothing was published"
    # A slug that only starts like one is free.
    created = await client.post("/api/v1/orgs/varga/skills", json={"files": _files(f"{slug}-tools")})
    assert created.status_code == 201, created.text


async def test_a_skill_published_under_that_slug_before_keeps_its_next_versions(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An organisation that already has one is not locked out of it: its next versions still publish."""
    from choregos_api.services import skills

    with monkeypatch.context() as before:
        before.setattr(skills, "RESERVED_SKILL_SLUGS", frozenset())
        assert (
            await client.post("/api/v1/orgs/varga/skills", json={"files": _files("library")})
        ).status_code == 201
    again = await client.post(
        "/api/v1/orgs/varga/skills/library/versions",
        json={"files": {**_files("library"), "references/more.md": "- one more\n"}},
    )
    assert again.status_code == 201, again.text
    assert again.json()["version"] == 2
    imported = await _import(client, {**_files("library"), "references/more.md": "- two more\n"})
    assert imported.status_code == 201, imported.text
    assert imported.json()["latest_version"] == 3
