"""The one organisation lookup of the routers (S47-09, ADR 0099)."""

from __future__ import annotations

import pytest
from tranibor_api.db.session import session_scope
from tranibor_api.errors import ApiError, not_found
from tranibor_api.services.organisations import organisation_by_slug


async def test_an_unknown_organisation_is_the_same_404(org: str) -> None:
    async with session_scope(orgs="*") as session:  # the platform reads every organisation
        found = await organisation_by_slug(session, org)
        assert found.slug == "northwind"
        with pytest.raises(ApiError) as caught:
            await organisation_by_slug(session, "nope")
    expected = not_found("Organisation", "nope")
    assert caught.value.status_code == 404
    assert caught.value.detail == expected.detail
