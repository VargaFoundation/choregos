"""Skills imported from a git repository at a pinned commit (S32-05, ADR 0050).

No test reaches GitHub: repositories are recorded tarballs served by `FakeSourceFetcher`, and the
HTTPS fetcher is driven through an `httpx.MockTransport`.
"""

from __future__ import annotations

import io
import tarfile
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from httpx import ASGITransport, AsyncClient

from .conftest import login

SHA1 = "1" * 40
SHA2 = "2" * 40
ANTHROPICS = "https://github.com/anthropics/skills"
APACHE = "                    Apache License\n               Version 2.0, January 2004\n"
ONBOARDING = """---
name: onboarding
description: The steps of a newcomer's first day.
---

# Onboarding
"""


def _skill_md(name: str, extra: str = "") -> str:
    return f"---\nname: {name}\ndescription: The {name} skill.\n{extra}---\n\n# {name}\n"


def tarball(
    files: dict[str, str | bytes],
    *,
    top: str = "anthropics-skills-1111111",
    links: tuple[str, ...] = (),
    executables: tuple[str, ...] = (),
) -> bytes:
    """A repository tarball as GitHub serves it: one top folder, then the repository."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path, content in files.items():
            data = content.encode("utf-8") if isinstance(content, str) else content
            info = tarfile.TarInfo(f"{top}/{path}")
            info.size = len(data)
            info.mode = 0o755 if path in executables else 0o644
            archive.addfile(info, io.BytesIO(data))
        for path in links:
            info = tarfile.TarInfo(f"{top}/{path}")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            archive.addfile(info)
    return buffer.getvalue()


REPO_V1 = {
    "LICENSE": APACHE,
    "README.md": "# Skills\n",
    "skills/onboarding/SKILL.md": ONBOARDING,
    "skills/onboarding/references/groups.md": "- everyone\n",
    "skills/pdf/SKILL.md": _skill_md("pdf"),
    "skills/pdf/scripts/fill.py": "print('fill')\n",
    "skills/greedy/SKILL.md": _skill_md("greedy", "allowed-tools: [Bash]\n"),
    "skills/linked/SKILL.md": _skill_md("linked"),
    "skills/fonts/SKILL.md": _skill_md("fonts"),
    "skills/fonts/font.ttf": b"\x00\x01\xff\xfe",
}
REPO_V2 = {
    **REPO_V1,
    "skills/onboarding/SKILL.md": ONBOARDING + "\n2. Open the VPN.\n",
    "skills/onboarding/references/vpn.md": "- vpn\n",
}


@pytest.fixture
def fake() -> Iterator[Any]:
    from choregos_api.services.skill_sources import FakeSourceFetcher, set_fetcher

    fetcher = FakeSourceFetcher()
    fetcher.add(
        ANTHROPICS,
        SHA1,
        tarball(
            REPO_V1, links=("skills/linked/references/passwd",), executables=("skills/pdf/scripts/fill.py",)
        ),
        refs={"v1": SHA1},
    )
    fetcher.add(
        ANTHROPICS, SHA2, tarball(REPO_V2, top="anthropics-skills-2222222"), refs={"main": SHA2, "HEAD": SHA2}
    )
    fetcher.add("https://github.com/elsewhere/skills", SHA1, tarball(REPO_V1), refs={"main": SHA1})
    set_fetcher(fetcher)
    yield fetcher
    set_fetcher(None)


async def _member(email: str, role: str) -> None:
    from choregos_api.db.models import Membership, Organization, User
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as session:
        org = (await session.execute(select(Organization).where(Organization.slug == "varga"))).scalar_one()
        user = User(email=email, display_name=email.split("@", maxsplit=1)[0])
        session.add(user)
        await session.flush()
        session.add(Membership(user_id=user.id, org_id=org.id, role=role))


async def _import(
    client: AsyncClient,
    repo: str = ANTHROPICS,
    sha: str = SHA1,
    skills: list[str] | None = None,
    path: str = "skills",
) -> httpx.Response:
    return await client.post(
        "/api/v1/orgs/varga/skills/import-git",
        json={"repo_url": repo, "sha": sha, "path": path, "skills": skills or ["onboarding"]},
    )


async def test_the_preview_resolves_a_branch_to_its_commit_and_lists_each_skill(
    client: AsyncClient, admin: str, fake: Any
) -> None:
    preview = await client.post(
        "/api/v1/orgs/varga/skills/import-git/preview",
        json={"repo_url": ANTHROPICS + ".git", "ref": "main", "path": "skills"},
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert (body["repo"], body["sha"], body["path"], body["allowed"]) == (ANTHROPICS, SHA2, "skills", True)
    by_path = {skill["path"]: skill for skill in body["skills"]}
    assert set(by_path) == {
        "skills/onboarding",
        "skills/pdf",
        "skills/greedy",
        "skills/linked",
        "skills/fonts",
    }
    assert by_path["skills/onboarding"]["name"] == "onboarding"
    assert by_path["skills/onboarding"]["problem"] is None
    assert by_path["skills/onboarding"]["files"] == 3
    assert by_path["skills/onboarding"]["licence"] == "Apache-2.0"
    # The gate, before anything is written (S32-06): references are medium, a script is high.
    assert by_path["skills/onboarding"]["risk"] == "medium"
    assert by_path["skills/pdf"]["risk"] == "high"
    assert "script" in {f["code"] for f in by_path["skills/pdf"]["findings"]}
    assert "declares no permission" in by_path["skills/greedy"]["problem"]
    assert "not UTF-8" in by_path["skills/fonts"]["problem"]
    assert (await client.get("/api/v1/orgs/varga/skills")).json() == [], "a preview writes nothing"


async def test_a_link_inside_a_skill_refuses_that_skill(client: AsyncClient, admin: str, fake: Any) -> None:
    preview = await client.post(
        "/api/v1/orgs/varga/skills/import-git/preview",
        json={"repo_url": ANTHROPICS, "ref": "v1", "path": "skills"},
    )
    linked = next(s for s in preview.json()["skills"] if s["path"] == "skills/linked")
    assert "link" in linked["problem"]
    assert (await _import(client, skills=["linked"])).status_code == 422


@pytest.mark.parametrize("ref", ["main", "v1", SHA1[:12], SHA1.upper() + "0", "HEAD"])
async def test_a_branch_a_tag_or_a_short_sha_is_refused(
    client: AsyncClient, admin: str, fake: Any, ref: str
) -> None:
    refused = await _import(client, sha=ref)
    assert refused.status_code == 422, refused.text
    assert "not a commit SHA" in refused.text
    assert not [call for call in fake.calls if call[0] == "tarball"], (
        "nothing is fetched for a moving reference"
    )
    assert (await client.get("/api/v1/orgs/varga/skills")).json() == []


async def test_an_import_pins_the_commit_and_records_where_the_skill_comes_from(
    client: AsyncClient, admin: str, fake: Any
) -> None:
    imported = await _import(client)
    assert imported.status_code == 201, imported.text
    (skill,) = imported.json()
    assert (skill["slug"], skill["latest_version"]) == ("onboarding", 1)
    version = (await client.get("/api/v1/orgs/varga/skills/onboarding/versions/1")).json()
    assert version["files"] == {
        "SKILL.md": ONBOARDING,
        "references/groups.md": "- everyone\n",
    }
    assert version["source"] == {
        "repo": ANTHROPICS,
        "sha": SHA1,
        "path": "skills/onboarding",
        "licence": "Apache-2.0",
    }
    assert ("tarball", "github.com/anthropics/skills", SHA1) in fake.calls


async def test_a_refused_or_unknown_skill_is_not_imported(client: AsyncClient, admin: str, fake: Any) -> None:
    greedy = await _import(client, skills=["onboarding", "greedy"])
    assert greedy.status_code == 422 and "declares no permission" in greedy.text
    assert (await _import(client, skills=["absent"])).status_code == 422
    assert (await client.get("/api/v1/orgs/varga/skills")).json() == [], "all or nothing"


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/anthropics/skills",
        "https://evil.example/anthropics/skills",
        "https://token@github.com/anthropics/skills",
        "https://github.com:8443/anthropics/skills",
        "https://github.com/anthropics",
        "https://github.com/anthropics/skills?x=1",
        "https://169.254.169.254/latest/meta-data",
        "file:///etc/passwd",
    ],
)
async def test_only_a_github_or_gitlab_repository_is_fetched(
    client: AsyncClient, admin: str, fake: Any, url: str
) -> None:
    refused = await client.post(
        "/api/v1/orgs/varga/skills/import-git/preview", json={"repo_url": url, "ref": "main"}
    )
    assert refused.status_code == 422, refused.text
    assert fake.calls == [], "no request leaves for a refused URL"


async def test_an_unknown_repository_is_an_upstream_failure(
    client: AsyncClient, admin: str, fake: Any
) -> None:
    missing = await client.post(
        "/api/v1/orgs/varga/skills/import-git/preview",
        json={"repo_url": "https://github.com/anthropics/nothing", "ref": "main"},
    )
    assert missing.status_code == 502, missing.text


async def test_a_project_owner_imports_new_skills_from_the_allowlist_only(
    client: AsyncClient, admin: str, fake: Any
) -> None:
    await _member("owner@varga.dev", "project_owner")
    await _member("dev@varga.dev", "developer")
    async with AsyncClient(
        transport=ASGITransport(app=client._transport.app), base_url="http://test"
    ) as owner:  # type: ignore[attr-defined]
        await login(owner, "owner@varga.dev")
        assert (await _import(owner)).status_code == 201, "anthropics/skills is allowlisted by default"
        elsewhere = await _import(owner, repo="https://github.com/elsewhere/skills", skills=["pdf"])
        assert elsewhere.status_code == 403 and "allowlist" in elsewhere.text
        again = await _import(owner, sha=SHA2)
        assert again.status_code == 403 and "next version" in again.text, (
            "a new version changes what the agents carrying the skill read: an administrator's call"
        )
    async with AsyncClient(transport=ASGITransport(app=client._transport.app), base_url="http://test") as dev:  # type: ignore[attr-defined]
        await login(dev, "dev@varga.dev")
        assert (await _import(dev, skills=["pdf"])).status_code == 403
    assert (
        await _import(client, repo="https://github.com/elsewhere/skills", skills=["pdf"])
    ).status_code == 201
    assert (await _import(client, sha=SHA2)).json()[0]["latest_version"] == 2


async def test_the_allowlist_is_the_administrators(
    client: AsyncClient, project: dict[str, Any], fake: Any
) -> None:
    await client.put(
        f"/api/v1/projects/{project['id']}/connectors/scm",
        json={"type": "github", "config": {"repo": "varga/billing-api"}},
    )
    defaults = (await client.get("/api/v1/orgs/varga/skill-sources")).json()
    assert defaults == {"sources": ["github.com/anthropics/skills", "github.com/varga"], "default": True}
    written = await client.put(
        "/api/v1/orgs/varga/skill-sources", json={"sources": ["github.com/elsewhere", "gitlab.com/team/sub"]}
    )
    assert written.status_code == 200, written.text
    assert (await client.get("/api/v1/orgs/varga/skill-sources")).json()["default"] is False
    preview = await client.post(
        "/api/v1/orgs/varga/skills/import-git/preview",
        json={"repo_url": "https://github.com/elsewhere/skills", "ref": "main", "path": "skills"},
    )
    assert preview.json()["allowed"] is True
    bad = await client.put("/api/v1/orgs/varga/skill-sources", json={"sources": ["evil.example/x"]})
    assert bad.status_code == 422
    await _member("owner@varga.dev", "project_owner")
    async with AsyncClient(
        transport=ASGITransport(app=client._transport.app), base_url="http://test"
    ) as owner:  # type: ignore[attr-defined]
        await login(owner, "owner@varga.dev")
        refused = await owner.put("/api/v1/orgs/varga/skill-sources", json={"sources": ["github.com/x"]})
        assert refused.status_code == 403


async def test_an_update_is_proposed_never_applied(client: AsyncClient, admin: str, fake: Any) -> None:
    await _import(client)
    proposal = await client.get("/api/v1/orgs/varga/skills/onboarding/update")
    assert proposal.status_code == 200, proposal.text
    body = proposal.json()
    assert (body["sha"], body["up_to_date"], body["version"]) == (SHA2, False, 1)
    assert (body["added"], body["removed"], body["modified"]) == (["references/vpn.md"], [], ["SKILL.md"])
    skill = (await client.get("/api/v1/orgs/varga/skills/onboarding")).json()
    assert skill["latest_version"] == 1, "checking for an update publishes nothing"
    same = (await client.get("/api/v1/orgs/varga/skills/onboarding/update", params={"ref": "v1"})).json()
    assert same["up_to_date"] is True and same["sha"] == SHA1
    # Applying it is an import of the proposed SHA, by a person.
    assert (await _import(client, sha=body["sha"])).json()[0]["latest_version"] == 2


async def test_a_skill_not_from_git_has_no_update(client: AsyncClient, admin: str, fake: Any) -> None:
    await client.post("/api/v1/orgs/varga/skills", json={"files": {"SKILL.md": ONBOARDING}})
    assert (await client.get("/api/v1/orgs/varga/skills/onboarding/update")).status_code == 422


# ───────────────────────────── the tarball and the network, alone ─────────────────────────────


def test_an_entry_leaving_the_repository_refuses_the_tarball() -> None:
    from choregos_api.services.skill_sources import SourceRefused, read_tarball

    with pytest.raises(SourceRefused, match="outside"):
        read_tarball(tarball({"SKILL.md": ONBOARDING, "../../etc/cron.d/x": "* * * * * root sh"}), "")


def test_a_path_leaving_the_repository_is_refused() -> None:
    from choregos_api.services.skill_sources import SourceRefused, read_tarball

    with pytest.raises(SourceRefused):
        read_tarball(tarball(REPO_V1), "../")


def test_the_licence_is_read_from_the_skill_then_the_repository() -> None:
    from choregos_api.services.skill_sources import detect_licence

    assert detect_licence({"LICENSE.txt": "Proprietary. All rights reserved."}, {"LICENSE": APACHE}) == (
        "LicenseRef-proprietary"
    )
    assert detect_licence({}, {"LICENSE": "MIT License\n\nPermission is hereby granted"}) == "MIT"
    assert detect_licence({"SKILL.md": ONBOARDING}, {}) is None


async def test_https_resolves_through_the_api_and_follows_no_redirect() -> None:
    from choregos_api.services.skill_sources import (
        HttpsSourceFetcher,
        SourceRefused,
        SourceUnavailable,
        parse_repo_url,
    )

    requests: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.host == "api.github.com":
            if request.url.path.endswith("/commits/main"):
                return httpx.Response(200, text=SHA1)
            return httpx.Response(404)
        if request.url.path.endswith(SHA1):
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})
        return httpx.Response(200, content=b"x" * 2048)

    fetcher = HttpsSourceFetcher(max_bytes=1024, transport=httpx.MockTransport(answer))
    source = parse_repo_url(ANTHROPICS)
    assert await fetcher.resolve(source, "main") == SHA1
    with pytest.raises(SourceRefused):
        await fetcher.resolve(source, "nope")
    with pytest.raises(SourceUnavailable, match="302"):
        await fetcher.tarball(source, SHA1)
    with pytest.raises(SourceRefused, match="larger than"):
        await fetcher.tarball(source, SHA2)
    assert not any("169.254" in url for url in requests), "a redirect is not followed"
    assert requests[0] == "https://api.github.com/repos/anthropics/skills/commits/main"
