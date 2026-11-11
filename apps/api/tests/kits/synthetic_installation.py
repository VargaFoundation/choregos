"""For tests whose subject is not an installation's readiness (S63-04, wave E).

A conversation re-reads its installation at every turn and is refused when it is no longer `ready`
(`services/conversation_installation.py`). A test that inserts an `assistant_installations` row by hand,
with no plugin behind it, would be `suspended`: this fixture lets such a row talk, by skipping that one
check. The check has its own tests (`test_conversation_policy.py`), which do NOT use this fixture.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture
def synthetic_installation(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    from choregos_api.services import conversations

    async def skip(session: object, record: object) -> None:
        return None

    monkeypatch.setattr(conversations, "apply_installation", skip)
    yield
