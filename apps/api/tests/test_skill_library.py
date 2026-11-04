"""The skill library (ADR 0033, S18-04).

A skill is a folder that an agent carries. It declares no permission, does not leave its folder,
and its versions are not rewritten.
"""

from __future__ import annotations

import io
import stat
import zipfile
from typing import Any

import pytest
from httpx import AsyncClient

from .kits.skill_library import SKILL_FILES, SKILL_MD


def _zip(files: dict[str, str], *, link: str | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in files.items():
            archive.writestr(path, text)
        if link:
            info = zipfile.ZipInfo(link)
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, "/etc/passwd")
    return buffer.getvalue()


async def _import(client: AsyncClient, content: bytes) -> Any:
    return await client.post(
        "/api/v1/orgs/varga/skills/import", content=content, headers={"Content-Type": "application/zip"}
    )


async def test_a_skill_is_born_and_its_versions_are_not_rewritten(client: AsyncClient, admin: str) -> None:
    created = await client.post("/api/v1/orgs/varga/skills", json={"files": SKILL_FILES})
    assert created.status_code == 201, created.text
    assert (created.json()["slug"], created.json()["latest_version"]) == ("procedure-onboarding", 1)
    v1 = (await client.get("/api/v1/orgs/varga/skills/procedure-onboarding/versions/1")).json()
    assert v1["files"] == SKILL_FILES and v1["digest"].startswith("sha256:")
    v2 = await client.post(
        "/api/v1/orgs/varga/skills/procedure-onboarding/versions",
        json={"files": {**SKILL_FILES, "references/groups.md": "- all-staff\n"}},
    )
    assert v2.status_code == 201 and v2.json()["version"] == 2
    assert (await client.get("/api/v1/orgs/varga/skills/procedure-onboarding/versions/1")).json() == v1
    assert (await client.post("/api/v1/orgs/varga/skills", json={"files": SKILL_FILES})).status_code == 409


async def test_an_archive_with_its_root_folder_imports(client: AsyncClient, admin: str) -> None:
    imported = await _import(client, _zip({f"procedure-onboarding/{c}": t for c, t in SKILL_FILES.items()}))
    assert imported.status_code == 201, imported.text
    files = (await client.get("/api/v1/orgs/varga/skills/procedure-onboarding/versions/1")).json()["files"]
    assert set(files) == {"SKILL.md", "references/groups.md"}
    # The same archive, imported again: the next version, not a duplicate.
    assert (await _import(client, _zip(SKILL_FILES))).json()["latest_version"] == 2


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        pytest.param(_zip({**SKILL_FILES, "../../escape.sh": "rm -rf /"}), "goes outside", id="zip-slip"),
        pytest.param(_zip(SKILL_FILES, link="references/link"), "symbolic link", id="symbolic-link"),
        pytest.param(
            _zip({"SKILL.md": SKILL_MD.replace("description:", "allowed-tools: [Bash]\ndescription:")}),
            "declares no permission",
            id="allowed-tools",
        ),
        pytest.param(
            _zip({"SKILL.md": SKILL_MD.replace("name: procedure-onboarding\n", "")}), "name", id="no-name"
        ),
        pytest.param(_zip({"read-me.md": "x"}), "SKILL.md is missing", id="no-skill-md"),
        pytest.param(_zip({**SKILL_FILES, **{f"{i}": "x" for i in range(256)}}), "256", id="too-many-files"),
        pytest.param(_zip({**SKILL_FILES, "big.md": "x" * (5 * 1024 * 1024 + 1)}), "KiB", id="too-big"),
        pytest.param(b"not an archive", "zip", id="not-a-zip"),
    ],
)
async def test_what_an_archive_does_not_let_through(
    client: AsyncClient, admin: str, content: bytes, reason: str
) -> None:
    refused = await _import(client, content)
    assert refused.status_code == 422, refused.text
    assert reason in refused.text
    assert (await client.get("/api/v1/orgs/varga/skills")).json() == [], "nothing is created"


async def test_the_name_of_the_next_version_must_be_the_skills(client: AsyncClient, admin: str) -> None:
    await client.post("/api/v1/orgs/varga/skills", json={"files": SKILL_FILES})
    other = {"SKILL.md": SKILL_MD.replace("name: procedure-onboarding", "name: something-else")}
    refused = await client.post(
        "/api/v1/orgs/varga/skills/procedure-onboarding/versions", json={"files": other}
    )
    assert refused.status_code == 422 and "must match" in refused.text


async def test_a_skill_says_which_agents_carry_it(client: AsyncClient, admin: str) -> None:
    await client.post("/api/v1/orgs/varga/skills", json={"files": SKILL_FILES})
    agent = await client.post(
        "/api/v1/orgs/varga/agents",
        json={
            "slug": "onboarding-coordinator",
            "display_name": "Coordinator",
            "spec": {"skills": [{"slug": "procedure-onboarding"}]},
        },
    )
    assert agent.status_code == 201, agent.text
    skill = (await client.get("/api/v1/orgs/varga/skills/procedure-onboarding")).json()
    assert skill["used_by"] == ["onboarding-coordinator@1"]


async def test_the_runner_reads_the_skills_of_its_run_and_of_no_other(
    client: AsyncClient, project: dict[str, Any]
) -> None:
    """`GET /internal/runs/{id}/skills`: the files the `StageInput` names, with the run's token."""
    from choregos_api.db.models import Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.security import mint_run_token

    created = (await client.post("/api/v1/orgs/varga/skills", json={"files": SKILL_FILES})).json()
    digest = created["versions"][0]["digest"]
    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="BILLING-9", title="T", state="ready")
        session.add(item)
        await session.flush()
        for run_id, skills in (
            ("run-with", [{"slug": "procedure-onboarding", "version": 1, "digest": digest}]),
            ("run-without", []),
        ):
            session.add(
                Run(
                    id=run_id,
                    work_item_id=item.id,
                    project_id=project["id"],
                    stage_role="implement",
                    status="running",
                    stage_input={"skills": skills},
                )
            )
    client.cookies.clear()
    token = mint_run_token("run-with", project_slug="billing-api", work_item_key="BILLING-9", ttl_minutes=30)
    read_back = await client.get(
        "/api/v1/internal/runs/run-with/skills", headers={"Authorization": f"Bearer {token}"}
    )
    assert read_back.status_code == 200, read_back.text
    (skill,) = read_back.json()
    assert (skill["slug"], skill["version"], skill["digest"], skill["files"]) == (
        "procedure-onboarding",
        1,
        digest,
        SKILL_FILES,
    )
    other = await client.get(
        "/api/v1/internal/runs/run-without/skills", headers={"Authorization": f"Bearer {token}"}
    )
    assert other.status_code in {401, 403}, "a run's token does not read another run"
