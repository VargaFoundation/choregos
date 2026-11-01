"""The six backends and their capabilities are declared once, in choregos_core.models."""

from __future__ import annotations

from choregos_core.models import KNOWN_BACKEND_CAPABILITIES, KNOWN_BACKEND_NAMES


def test_the_names_follow_the_capability_table() -> None:
    assert KNOWN_BACKEND_NAMES == (
        "claude-code",
        "codex",
        "gemini-cli",
        "goose",
        "opencode",
        "copilot-cli",
    )
    assert tuple(name for name, _ in KNOWN_BACKEND_CAPABILITIES) == KNOWN_BACKEND_NAMES
    capabilities = dict(KNOWN_BACKEND_CAPABILITIES)
    assert "structured_output" in capabilities["claude-code"]
