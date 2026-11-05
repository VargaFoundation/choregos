"""What a plugin, a template or the agent catalogue installs may not take a console page's slug (S55-18).

A person's skill was refused `library` and `catalogue` (S55-17), but the installs write skills through
another path, `install_skills_and_agents`: a plugin that shipped a skill of that slug created a skill
nobody could open, its page being the skills library (`/skills/library`) or a redirect to it. Every
path now asks the same check (`skills.refuse_reserved_slug`): the install is refused, in the existing
422 shape, before anything is written; a skill the organisation already has under that slug is left
alone, as before.
"""

from __future__ import annotations

import dataclasses
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select

from .kits.skill_library import SKILL_FILES, SKILL_MD

ORG = "/api/v1/orgs/varga"
HELLO = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "plugins" / "hello"
ALONE = {"source": "offline", "name": "hello", "version": "1.0.0"}


def _files(slug: str) -> dict[str, str]:
    return {**SKILL_FILES, "SKILL.md": SKILL_MD.replace("name: procedure-onboarding", f"name: {slug}")}


def _refused(problem: dict[str, Any], what: str, slug: str) -> None:
    assert problem["detail"].startswith(f"{what} ships the skill `{slug}`, and `{slug}` is reserved"), problem
    assert problem["errors"] == [
        {"loc": ["SKILL.md", "name"], "msg": f"`{slug}` is reserved", "code": "skill.slug_reserved"}
    ]


async def _skills() -> dict[str, int]:
    """Each skill of the organisation and its number of versions."""
    from choregos_api.db.models import Skill, SkillVersion
    from choregos_api.db.session import session_scope

    async with session_scope() as session:
        rows = await session.execute(
            select(Skill.slug, func.count(SkillVersion.id))
            .outerjoin(SkillVersion, SkillVersion.skill_id == Skill.id)
            .group_by(Skill.slug)
        )
        # Not `dict(rows.tuples())`: a result has `keys()`, so `dict` would read it as a mapping.
        return {str(slug): int(count) for slug, count in rows.tuples()}


@pytest.fixture
def hello_shipping(tmp_path: Path) -> Iterator[Any]:
    """The `hello` plugin, without its server (one administrator installs it), its skill renamed."""
    from choregos_api.services.plugin_supply import OfflineDirectory, use_plugin_supply

    def ship(slug: str) -> None:
        target = tmp_path / "plugins" / "hello" / "1.0.0"
        shutil.rmtree(target, ignore_errors=True)
        shutil.copytree(HELLO, target)
        shutil.rmtree(target / "servers")
        manifest = target / "plugin.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace("  servers: [servers/hello.yaml]\n", ""),
            encoding="utf-8",
        )
        skill = target / "skills" / "greeting" / "SKILL.md"
        skill.write_text(
            skill.read_text(encoding="utf-8").replace("name: greeting", f"name: {slug}"), encoding="utf-8"
        )

    use_plugin_supply(OfflineDirectory(tmp_path / "plugins"))
    yield ship
    use_plugin_supply(None)


@pytest.mark.parametrize("slug", ["library", "catalogue"])
async def test_a_plugin_shipping_a_skill_under_a_console_page_s_slug_is_refused(
    client: AsyncClient, admin: str, hello_shipping: Any, slug: str
) -> None:
    hello_shipping(slug)
    before = await _skills()
    refused = await client.post(f"{ORG}/plugins/install", json=ALONE)
    assert refused.status_code == 422, refused.text
    _refused(refused.json(), "the plugin `hello`", slug)
    assert await _skills() == before, "nothing was written"
    assert (await client.get(f"{ORG}/plugins")).json()["items"] == []
    # The same plugin, its skill under a free slug, installs.
    hello_shipping(f"{slug}-tools")
    installed = await client.post(f"{ORG}/plugins/install", json=ALONE)
    assert installed.status_code == 201, installed.text
    assert installed.json()["contents"]["skills"] == [f"{slug}-tools"]


async def test_the_agent_catalogue_installing_a_skill_under_that_slug_is_refused(
    client: AsyncClient, admin: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The architect's catalogue entry, naming a skill `library` its plugin ships."""
    from choregos_api.services import agent_catalogue as service
    from choregos_core.agent_catalogue import CatalogueSkill

    base = service.offline_catalogue()
    architect = next(e for e in base.entries if e.slug == "architect")
    shipped = CatalogueSkill("library", architect.plugin, base.plugins[architect.plugin], _files("library"))
    view = dataclasses.replace(
        base,
        entries=tuple(
            dataclasses.replace(e, skills=("library",)) if e is architect else e for e in base.entries
        ),
        skills={**base.skills, "library": shipped},
    )
    monkeypatch.setattr(service, "offline_catalogue", lambda: view)
    before = await _skills()
    refused = await client.post(f"{ORG}/agent-catalogue/architect/install")
    assert refused.status_code == 422, refused.text
    _refused(refused.json(), "the catalogue entry `architect`", "library")
    assert await _skills() == before
    assert "architect" not in {a["slug"] for a in (await client.get(f"{ORG}/agents")).json()}


async def test_the_shared_install_path_refuses_before_writing_and_spares_an_existing_skill(
    client: AsyncClient, admin: str
) -> None:
    """`install_skills_and_agents`, the path a template's skills take (S20-07): a new skill under a
    reserved slug refuses the whole install, the skills before it included; one the organisation
    already has under that slug is kept, as an install never rewrites a skill."""
    from choregos_api.db.models import Skill
    from choregos_api.db.session import session_scope
    from choregos_api.errors import ApiError
    from choregos_api.services.installation import install_skills_and_agents
    from choregos_api.services.plugin_installer import org_id_of

    what = "the template `hello-flow`"
    async with session_scope() as session:
        org_id = await org_id_of(session, "varga")
        with pytest.raises(ApiError) as refusal:
            await install_skills_and_agents(
                session,
                org_id,
                [_files("first-one"), _files("catalogue")],
                [],
                "template:hello-flow",
                what=what,
            )
    assert refusal.value.status_code == 422
    _refused({"detail": refusal.value.detail, "errors": refusal.value.errors}, what, "catalogue")
    assert "first-one" not in await _skills(), "refused before the first skill was written"

    async with session_scope() as session:
        org_id = await org_id_of(session, "varga")
        session.add(
            Skill(org_id=org_id, slug="catalogue", description="Published before S55-17", status="active")
        )
    async with session_scope() as session:
        org_id = await org_id_of(session, "varga")
        installed = await install_skills_and_agents(
            session, org_id, [_files("first-one"), _files("catalogue")], [], "template:hello-flow", what=what
        )
    assert installed["skills"] == ["first-one"]
    assert (await _skills())["catalogue"] == 0, "the organisation's own is not rewritten"
