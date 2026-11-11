"""Helpers shared by the assistant rights tests (S63-15)."""

from __future__ import annotations

from typing import Any

from httpx import ASGITransport, AsyncClient

from ..conftest import login
from ..kits.assistant_install import (
    offline,  # noqa: F401
)
from ..test_agent_sessions import Upstream, upstream  # noqa: F401

ME = "/api/v1/me"


OPEN = f"{ME}/assistants/varga/investment-assistant/conversations"


async def _as(app: Any, email: str) -> AsyncClient:
    person = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
    await login(person, email)
    return person
