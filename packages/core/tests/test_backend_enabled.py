"""One default for "is this coding-agent backend on?", shared by the API and the orchestrator."""

from __future__ import annotations

from choregos_core import KNOWN_BACKEND_NAMES, backend_enabled


def test_no_stored_setting_enables_claude_code_only() -> None:
    assert backend_enabled("claude-code", None)
    assert [n for n in KNOWN_BACKEND_NAMES if backend_enabled(n, None)] == ["claude-code"]


def test_a_stored_setting_wins() -> None:
    assert not backend_enabled("claude-code", False)
    assert backend_enabled("gemini-cli", True)
