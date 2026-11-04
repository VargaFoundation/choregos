"""What the deployment announces must be what is loaded (ADR 0024).

The running edition is not a setting: it is the enterprise extension that declares it when it
loads (`edition.declare`). The chart, for its part, ANNOUNCES an edition (`global.edition` ->
`CHOREGOS_EDITION`). Nothing linked the two, and the gap was silent: a platform announced as
enterprise on community images starts, looks healthy, exposes `/metrics`, and refuses the second
organisation weeks later, a symptom with no apparent relation to its cause.
"""

from __future__ import annotations

from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _clean_edition() -> Any:
    """Each test starts from the most restrictive edition: the state is a module."""
    from choregos_api.edition import reset

    reset()
    yield
    reset()


def test_announcing_enterprise_without_an_extension_refuses_to_start(monkeypatch: Any) -> None:
    """The refusal is at start-up, and it names the three possible causes."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_EDITION", "enterprise")
    reset_settings_cache()
    try:
        with pytest.raises(RuntimeError) as failure:
            create_app()
        message = str(failure.value)
        assert "global.edition=enterprise" in message
        assert "choregos-ee" in message, "the refusal must say which package is missing"
        assert "community" in message, "the refusal must say how to go back"
    finally:
        monkeypatch.delenv("CHOREGOS_EDITION", raising=False)
        reset_settings_cache()


def test_announcing_community_starts(monkeypatch: Any) -> None:
    """The default must require nothing: it is the edition of this repository."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_EDITION", "community")
    reset_settings_cache()
    try:
        assert create_app() is not None
    finally:
        monkeypatch.delenv("CHOREGOS_EDITION", raising=False)
        reset_settings_cache()


def test_announcing_enterprise_with_an_extension_that_declares_itself_starts(monkeypatch: Any) -> None:
    """An extension that calls `declare(ENTERPRISE)` lifts the refusal: this is the real case."""
    from choregos_api.config import reset_settings_cache
    from choregos_api.edition import ENTERPRISE, declare
    from choregos_api.main import create_app

    monkeypatch.setenv("CHOREGOS_EDITION", "enterprise")
    reset_settings_cache()
    declare(ENTERPRISE, features=frozenset({"multi_org"}))
    try:
        assert create_app() is not None
    finally:
        monkeypatch.delenv("CHOREGOS_EDITION", raising=False)
        reset_settings_cache()


@pytest.mark.asyncio
async def test_the_metrics_tell_the_edition(client: Any) -> None:
    """`choregos_edition_info`: without it, the metrics history does not say what was running.

    An operator looking at a dashboard from three weeks ago must be able to know which edition
    produced those figures. `GET /edition` only answers for today's process.
    """
    from choregos_api.metrics import refresh

    await refresh()
    body = (await client.get("/metrics")).text
    assert 'choregos_edition_info{edition="community",version=' in body
    assert body.count("choregos_edition_info{") == 1, "a single series: it is not a measurement"


@pytest.mark.asyncio
async def test_an_extension_can_replace_the_group_mapping(client: Any, monkeypatch: Any) -> None:
    """Seam C3: the enterprise edition changes the groups -> roles translation WITHOUT forking `auth.py`.

    The core knows a prefix (`choregos:<org>:<role>`) and a bare group for the default
    organisation. The enterprise edition wants another: a prefix PER organisation, groups coming
    from SAML or SCIM, a table editable in the interface. It is the only identity decision that
    is SUBSTITUTED; the rest (SAML, SCIM, session revocation) are new routes.
    """
    from choregos_api.edition import declare_group_mapper, group_mapper
    from choregos_api.routers.auth import roles_from_groups
    from choregos_contracts import Role

    # Without an extension, the core's mapper serves.
    assert group_mapper(roles_from_groups) is roles_from_groups

    seen: list[tuple[list[str], str]] = []

    def house_mapper(groups: list[str], default_org: str) -> dict[str, Role]:
        seen.append((list(groups), default_org))
        return {"acme": Role.ORG_ADMIN}

    declare_group_mapper(house_mapper)
    try:
        assert group_mapper(roles_from_groups) is house_mapper
        # And the sign-in really calls it: that is what tells a seam from a decorative setting.
        # The development sign-in REDIRECTS to its callback, and it is the callback that
        # translates the groups. Stopping at the 307 proved nothing.
        start = await client.get("/api/v1/auth/login", params={"as": "extension@varga.dev"})
        assert start.status_code in (302, 307), start.status_code
        await client.get(start.headers["location"])
        assert seen, "the declared mapper was not called by the sign-in"
    finally:
        declare_group_mapper(None)
        from choregos_api.edition import reset

        reset()

    # And the core takes over again as soon as the extension goes away.
    assert group_mapper(roles_from_groups) is roles_from_groups
