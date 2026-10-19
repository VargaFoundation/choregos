"""A skill may carry inert binaries, fonts and images, under a cap (S32-05b, ADR 0050 amendment).

Zip upload and git import share the rule: for the extensions of `INERT_BINARY_EXTENSIONS` the value
is always base64 of bytes whose magic matches; every other binary still refuses the skill.
"""

from __future__ import annotations

import base64
import io
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_contracts import MAX_BINARY_BYTES, empreinte_de_skill, skill_file_bytes
from httpx import AsyncClient

from .test_skills_from_git import ANTHROPICS, SHA1, _import, _skill_md, tarball

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x01\x02\xff" * 8
WEBP = b"RIFF\x04\x00\x00\x00WEBPVP8 "
SKILL_MD = _skill_md("branded")


def _zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, content in files.items():
            archive.writestr(path, content)
    return buffer.getvalue()


async def _upload(client: AsyncClient, content: bytes) -> Any:
    return await client.post(
        "/api/v1/orgs/varga/skills/import", content=content, headers={"Content-Type": "application/zip"}
    )


async def test_a_zip_with_an_image_is_stored_as_base64_and_digested_on_its_bytes(
    client: AsyncClient, admin: str
) -> None:
    created = await _upload(
        client, _zip({"SKILL.md": SKILL_MD, "assets/logo.png": PNG, "assets/a.webp": WEBP})
    )
    assert created.status_code == 201, created.text
    version = (await client.get("/api/v1/orgs/varga/skills/branded/versions/1")).json()
    assert version["files"]["assets/logo.png"] == base64.b64encode(PNG).decode()
    assert skill_file_bytes("assets/logo.png", version["files"]["assets/logo.png"]) == PNG
    assert version["digest"] == empreinte_de_skill(version["files"])
    # the digest is over the bytes: the same bytes under another base64 spelling are the same digest
    spelled = {**version["files"], "assets/logo.png": base64.b64encode(PNG).decode()}
    assert empreinte_de_skill(spelled) == version["digest"]


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        pytest.param(
            {"assets/logo.png": b"GIF89a-not-a-png"}, "does not look like a `.png`", id="magic-mismatch"
        ),
        pytest.param(
            {"assets/big.png": b"\x89PNG\r\n\x1a\n" + b"\0" * MAX_BINARY_BYTES},
            "512 KiB at most",
            id="too-big",
        ),
        pytest.param({"tool.bin": b"\x00\x01\xff\xfe"}, "not UTF-8", id="other-binary"),
        pytest.param({"lib/x.so": b"\x7fELF\x00\xff"}, "not UTF-8", id="executable-binary"),
        pytest.param(
            {"assets/a.webp": b"RIFF\x00\x00\x00\x00WAVE"}, "does not look like", id="riff-not-webp"
        ),
    ],
)
async def test_what_a_binary_must_hold_or_the_skill_is_refused(
    client: AsyncClient, admin: str, files: dict[str, bytes], reason: str
) -> None:
    refused = await _upload(client, _zip({"SKILL.md": SKILL_MD, **files}))
    assert refused.status_code == 422, refused.text
    assert reason in refused.text
    assert (await client.get("/api/v1/orgs/varga/skills")).json() == []


async def test_a_file_stored_directly_must_be_valid_base64_of_the_right_type(
    client: AsyncClient, admin: str
) -> None:
    for value in ("not base64 !!", "iVBORw0KGgo"):  # junk; valid-looking but unpadded and not a full PNG
        refused = await client.post(
            "/api/v1/orgs/varga/skills", json={"files": {"SKILL.md": SKILL_MD, "a.png": value}}
        )
        assert refused.status_code == 422, refused.text
    ok = await client.post(
        "/api/v1/orgs/varga/skills",
        json={"files": {"SKILL.md": SKILL_MD, "a.png": base64.b64encode(PNG).decode()}},
    )
    assert ok.status_code == 201, ok.text


async def test_svg_stays_text_and_a_text_file_may_start_with_base64(client: AsyncClient, admin: str) -> None:
    svg = '<svg xmlns="http://www.w3.org/2000/svg"/>'
    created = await _upload(client, _zip({"SKILL.md": SKILL_MD, "a.svg": svg, "notes.md": "base64:abc\n"}))
    assert created.status_code == 201, created.text
    files = (await client.get("/api/v1/orgs/varga/skills/branded/versions/1")).json()["files"]
    assert files["a.svg"] == svg and files["notes.md"] == "base64:abc\n"


async def test_the_total_cap_counts_decoded_bytes(client: AsyncClient, admin: str) -> None:
    # 11 PNGs of 500 KiB are 5.4 MiB decoded, but each is under the per-file cap
    chunk = b"\x89PNG\r\n\x1a\n" + b"\0" * (500 * 1024)
    files = {f"i/{n}.png": chunk for n in range(11)}
    refused = await _upload(client, _zip({"SKILL.md": SKILL_MD, **files}))
    assert refused.status_code == 422 and "KiB" in refused.text


@pytest.fixture
def fake() -> Iterator[Any]:
    from choregos_api.services.skill_sources import FakeSourceFetcher, set_fetcher

    fetcher = FakeSourceFetcher()
    repo = {
        "skills/branded/SKILL.md": SKILL_MD,
        "skills/branded/font.woff2": b"wOF2" + b"\0" * 16,
        "skills/bad/SKILL.md": _skill_md("bad"),
        "skills/bad/font.woff2": b"nope",
        "skills/blob/SKILL.md": _skill_md("blob"),
        "skills/blob/data.bin": b"\x00\xff",
    }
    fetcher.add(ANTHROPICS, SHA1, tarball(repo), refs={"v1": SHA1})
    set_fetcher(fetcher)
    yield fetcher
    set_fetcher(None)


async def test_a_git_import_accepts_a_font_and_refuses_a_fake_or_other_binary(
    client: AsyncClient, admin: str, fake: Any
) -> None:
    preview = await client.post(
        "/api/v1/orgs/varga/skills/import-git/preview",
        json={"repo_url": ANTHROPICS, "ref": SHA1, "path": "skills"},
    )
    assert preview.status_code == 200, preview.text
    by_path = {s["path"]: s for s in preview.json()["skills"]}
    assert by_path["skills/branded"]["problem"] is None
    assert by_path["skills/branded"]["bytes"] > 0
    assert "does not look like" in by_path["skills/bad"]["problem"]
    assert "not UTF-8" in by_path["skills/blob"]["problem"]
    imported = await _import(client, skills=["branded"])
    assert imported.status_code in (200, 201), imported.text
    files = (await client.get("/api/v1/orgs/varga/skills/branded/versions/1")).json()["files"]
    assert skill_file_bytes("font.woff2", files["font.woff2"]).startswith(b"wOF2")


def test_the_gate_does_not_scan_a_binary_and_a_binary_makes_a_skill_medium() -> None:
    from choregos_api.services.skill_gate import assess

    # `assess` does not validate base64 (`valider` does): a URL-shaped value would be a finding
    # in a text file, and must be none in a binary one.
    assessment = assess({"SKILL.md": SKILL_MD, "a.png": "https://evil.example/x"})
    assert assessment.risk == "medium"
    assert not [f for f in assessment.findings if f.file == "a.png"]
