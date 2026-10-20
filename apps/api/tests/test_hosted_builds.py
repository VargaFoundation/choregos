"""The build of a hosted agent's zip (S41-08, ADR 0080 §9): started by publication, once per key,
and a channel that takes the version only once its image is built.

The activities run as `HostedImageBuild` runs them, against the API's database, with a
`FakeImageBuilder`; the workflow itself is proved in the orchestrator's tests.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from choregos_adapters.fakes import FakeImageBuilder
from choregos_adapters.fakes.storage import FakeObjectStore
from httpx import AsyncClient
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from .test_hosted_sources import AGENT, AGENT_ZIP, ORG, _create, _runtime, _upload, _zip

REPOSITORY = "registry.internal/choregos-builds/varga"
OTHER_ZIP = _zip({"main.py": AGENT + "# the second one\n", "requirements.txt": "httpx==0.28.1\n"})
THIRD_ZIP = _zip({"main.py": AGENT + "# the third one\n", "requirements.txt": "httpx==0.28.1\n"})


@pytest.fixture
def store() -> Iterator[FakeObjectStore]:
    from choregos_api.object_store import use_object_store

    fake = FakeObjectStore()
    use_object_store(fake)
    yield fake
    use_object_store(None)


@pytest.fixture
def builder(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeImageBuilder]:
    from choregos_api.config import reset_settings_cache
    from choregos_api.services.hosted_builds import set_image_builder

    monkeypatch.setenv("CHOREGOS_HOSTED_BUILD_REPOSITORY", REPOSITORY)
    reset_settings_cache()
    fake = FakeImageBuilder()
    set_image_builder(fake)
    yield fake
    set_image_builder(None)
    reset_settings_cache()


def _builds() -> dict[str, dict[str, Any]]:
    """The `HostedImageBuild` workflows publication started, by id."""
    from choregos_api.temporal import get_temporal

    started: dict[str, dict[str, Any]] = get_temporal().started  # type: ignore[attr-defined]
    return {wid: payload for wid, payload in started.items() if wid.startswith("hosted-build-")}


async def _run(payload: dict[str, Any]) -> dict[str, Any]:
    """What `HostedImageBuild` does: claim, build, record the outcome."""
    from choregos_orchestrator.activities.hosted_build import (
        build_hosted_image,
        claim_hosted_build,
        record_hosted_build,
    )

    env = ActivityEnvironment()
    claimed = await env.run(claim_hosted_build, payload)
    if claimed["status"] != "building":
        return dict(claimed)
    try:
        built = await env.run(build_hosted_image, payload)
    except ApplicationError as exc:
        return dict(await env.run(record_hosted_build, {**payload, "status": "failed", "error": str(exc)}))
    return dict(await env.run(record_hosted_build, {**payload, **built, "status": "ready"}))


async def _save(client: AsyncClient, sha256: str, n: int) -> None:
    spec = {"instructions": f"Triage {{{{ ticket.title }}}}, revision {n}.", "runtime": _runtime(sha256)}
    saved = await client.post(f"{ORG}/agents/triager/versions", json=spec)
    assert saved.status_code == 201, saved.text


async def _agent(client: AsyncClient) -> dict[str, Any]:
    return dict((await client.get(f"{ORG}/agents/triager")).json())


async def _build_of(client: AsyncClient, version: int) -> dict[str, Any]:
    found = await client.get(f"{ORG}/agents/triager/versions/{version}")
    assert found.status_code == 200, found.text
    return dict(found.json()["spec"]["runtime"]["build"])


def _payload_for(key: str, attempt: int = 1) -> dict[str, Any]:
    (payload,) = [p for p in _builds().values() if p["key"] == key and p["attempt"] == attempt]
    return payload


async def test_publication_starts_one_build_and_production_waits_for_it(
    client: AsyncClient, admin: str, store: FakeObjectStore, builder: FakeImageBuilder
) -> None:
    sha256 = (await _upload(client)).json()["sha256"]
    assert (await _create(client, _runtime(sha256))).status_code == 201
    (payload,) = _builds().values()
    key = (await _build_of(client, 1))["key"]
    assert payload["key"] == key and payload["attempt"] == 1
    assert (await _agent(client))["stable_version"] is None, "nothing is in production before the image"

    outcome = await _run(payload)
    assert outcome == {"status": "ready", "moved": ["triager@v1"]}
    assert (await _agent(client))["stable_version"] == 1
    build = await _build_of(client, 1)
    assert build["status"] == "ready" and build["image"] == builder.images[key]
    assert build["provenance"]["builder"] == "fake" and build["provenance"]["source"] == sha256
    assert build["provenance"]["image"] == build["image"] and build["provenance"]["built_at"]
    request = builder.requests[-1]
    assert request.context == AGENT_ZIP and request.repository == REPOSITORY
    assert "FROM docker.io/library/python:3.13" in request.dockerfile


async def test_a_version_with_the_same_zip_starts_nothing_and_reaches_production_at_once(
    client: AsyncClient, admin: str, store: FakeObjectStore, builder: FakeImageBuilder
) -> None:
    sha256 = (await _upload(client)).json()["sha256"]
    await _create(client, _runtime(sha256))
    await _run(next(iter(_builds().values())))
    from choregos_api.temporal import get_temporal

    get_temporal().started.clear()  # type: ignore[attr-defined]
    await _save(client, sha256, 2)
    assert _builds() == {}, "the image of this key is built: no second build"
    assert (await _agent(client))["stable_version"] == 2
    assert (await _build_of(client, 2))["status"] == "ready"
    assert builder.builds == [(await _build_of(client, 1))["key"]]


async def test_a_build_replayed_after_it_was_recorded_changes_nothing(
    client: AsyncClient, admin: str, store: FakeObjectStore, builder: FakeImageBuilder
) -> None:
    from choregos_orchestrator.activities.hosted_build import build_hosted_image, record_hosted_build

    sha256 = (await _upload(client)).json()["sha256"]
    await _create(client, _runtime(sha256))
    payload = next(iter(_builds().values()))
    await _run(payload)
    assert await _run(payload) == {"status": "ready", "image": builder.images[payload["key"]]}
    recorded = await _build_of(client, 1)
    env = ActivityEnvironment()
    again = await env.run(build_hosted_image, payload)  # a retried activity, the record lost
    assert again["built"] is False and len(builder.builds) == 1, "the key is found built, not built twice"
    replay = await env.run(record_hosted_build, {**payload, **again, "status": "ready"})
    assert replay == {"status": "ready", "moved": []}
    assert await _build_of(client, 1) == recorded, "the outcome is written once"


async def test_a_release_of_a_version_still_building_is_refused(
    client: AsyncClient, admin: str, store: FakeObjectStore, builder: FakeImageBuilder
) -> None:
    sha256 = (await _upload(client)).json()["sha256"]
    await _create(client, _runtime(sha256))
    await _run(next(iter(_builds().values())))
    other = (await _upload(client, OTHER_ZIP)).json()["sha256"]
    await _save(client, other, 2)
    assert (await _agent(client))["stable_version"] == 1, "v2 waits for its image"
    assert (await _build_of(client, 2))["status"] == "pending"

    refused = await client.post(f"{ORG}/agents/triager/releases", json={"channel": "stable", "version": 2})
    assert refused.status_code == 409, refused.text
    by_name = {e["loc"][1]: e for e in refused.json()["errors"]}
    assert "build is pending" in by_name["hosted_build"]["msg"]
    readiness = await client.get(f"{ORG}/agents/triager/versions/2/readiness", params={"channel": "stable"})
    checks = {c["name"]: c for c in readiness.json()["checks"]}
    assert checks["hosted_build"]["passed"] is False
    assert (await _agent(client))["stable_version"] == 1


async def test_an_older_build_finishing_last_does_not_move_production_back(
    client: AsyncClient, admin: str, store: FakeObjectStore, builder: FakeImageBuilder
) -> None:
    sha256 = (await _upload(client)).json()["sha256"]
    await _create(client, _runtime(sha256))
    await _run(next(iter(_builds().values())))
    second = (await _upload(client, OTHER_ZIP)).json()["sha256"]
    await _save(client, second, 2)
    third = (await _upload(client, THIRD_ZIP)).json()["sha256"]
    await _save(client, third, 3)
    key2, key3 = (await _build_of(client, 2))["key"], (await _build_of(client, 3))["key"]

    assert (await _run(_payload_for(key3)))["moved"] == ["triager@v3"]
    assert (await _run(_payload_for(key2)))["moved"] == [], "v3 is in production: v2 does not replace it"
    assert (await _agent(client))["stable_version"] == 3
    assert (await _build_of(client, 2))["status"] == "ready", "its image is there all the same"


async def test_a_failed_build_says_why_and_a_new_publication_tries_again(
    client: AsyncClient, admin: str, store: FakeObjectStore, builder: FakeImageBuilder
) -> None:
    sha256 = (await _upload(client)).json()["sha256"]
    await _create(client, _runtime(sha256))
    key = (await _build_of(client, 1))["key"]
    builder.fail(key, "ERROR: No matching distribution found for httpx==0.28.1")
    assert (await _run(_payload_for(key)))["status"] == "failed"
    failed = await _build_of(client, 1)
    assert failed["status"] == "failed" and "No matching distribution" in failed["error"]
    assert failed["image"] is None and (await _agent(client))["stable_version"] is None

    builder.failures.clear()
    await _save(client, sha256, 2)
    retry = _payload_for(key, attempt=2)
    assert (await _build_of(client, 1))["status"] == "pending", "the build is tried again for both"
    from choregos_orchestrator.activities.hosted_build import claim_hosted_build

    stale = await ActivityEnvironment().run(claim_hosted_build, _payload_for(key, attempt=1))
    assert stale["status"] == "superseded", "the first attempt, replayed, builds nothing"
    assert (await _run(retry))["moved"] == ["triager@v2"]
    assert (await _agent(client))["stable_version"] == 2


async def test_a_deployment_without_a_build_repository_fails_the_build_with_the_reason(
    client: AsyncClient,
    admin: str,
    store: FakeObjectStore,
    builder: FakeImageBuilder,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_api.config import reset_settings_cache

    monkeypatch.setenv("CHOREGOS_HOSTED_BUILD_REPOSITORY", "")
    reset_settings_cache()
    sha256 = (await _upload(client)).json()["sha256"]
    await _create(client, _runtime(sha256))
    assert (await _run(next(iter(_builds().values()))))["status"] == "failed"
    build = await _build_of(client, 1)
    assert "no build repository is configured" in build["error"] and builder.requests == []


def test_only_a_zip_version_has_a_build() -> None:
    from choregos_api.services.hosted_builds import zip_build_key

    image = {"type": "image", "ref": "ghcr.io/acme/triager:1", "digest": "sha256:" + "c" * 64}
    assert zip_build_key({"runtime": {"kind": "hosted", "source": image}}) is None
    assert zip_build_key({"runtime": {"kind": "prompt"}}) is None
    assert zip_build_key(None) is None
