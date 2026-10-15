"""Shared pieces of the remote-agent tests (ADR 0062): a scriptable A2A agent and a sealing key.

Plain functions, not fixtures: each test file wraps them in two three-line fixtures, so that no
fixture is imported over its own name.
"""

from __future__ import annotations

import base64
import os
from collections.abc import Iterator

import pytest
from choregos_adapters.fakes.a2a import FakeA2aAgent


def set_sealing_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """The deployment's key: without it no secret can be sealed (ADR 0058)."""
    from choregos_core.redaction import forget_secrets

    monkeypatch.setenv("CHOREGOS_SECRETS_KEKS", f"k1:{base64.b64encode(os.urandom(32)).decode()}")
    forget_secrets()


def remote_agent() -> Iterator[FakeA2aAgent]:
    """The remote agent every outbound card fetch, DCR and token call of the API reaches."""
    from choregos_api.services.remote_agents import set_a2a_transport

    fake = FakeA2aAgent()
    set_a2a_transport(fake.transport())
    yield fake
    set_a2a_transport(None)
