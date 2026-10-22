# SPDX-License-Identifier: Apache-2.0
"""Registre des backends ACP livrés."""

from __future__ import annotations

from pathlib import Path

from .base import Backend, LaunchPlan
from .claude_code import ClaudeCodeBackend
from .code import CodeBackend
from .others import (
    CodexBackend,
    CopilotCliBackend,
    GeminiCliBackend,
    GooseBackend,
    OpenCodeBackend,
)
from .prompt import PromptBackend

BACKENDS: dict[str, type[Backend]] = {
    ClaudeCodeBackend.name: ClaudeCodeBackend,
    CodexBackend.name: CodexBackend,
    GeminiCliBackend.name: GeminiCliBackend,
    GooseBackend.name: GooseBackend,
    OpenCodeBackend.name: OpenCodeBackend,
    CopilotCliBackend.name: CopilotCliBackend,
    CodeBackend.name: CodeBackend,
    PromptBackend.name: PromptBackend,
}

# Backends retirés : un nom qu'on reconnaît, pour refuser avec la raison plutôt qu'avec un
# « backend inconnu » qui laisserait croire à une faute de frappe.
RETIRED: dict[str, str] = {
    "openhands": (
        "retired on 2026-09-21: OpenHands exposes no command-line ACP agent — neither in 0.59 "
        "(`serve` and `cli` only), nor in 1.x (no binary any more, an `agent-server` HTTP server). "
        "See docs/adr/0011-openhands-removed.md; the default is now `claude-code`."
    ),
}

VERSIONS_LOCK = Path(__file__).parent / "versions.lock"


def get_backend(name: str) -> Backend:
    """Instancie un backend par son nom ; une erreur explicite si le nom est inconnu."""
    if name in RETIRED:
        raise KeyError(f"backend {name} {RETIRED[name]}")
    backend_class = BACKENDS.get(name)
    if backend_class is None:
        raise KeyError(f"unknown backend: {name} (known: {', '.join(sorted(BACKENDS))})")
    return backend_class()


#: Backends a runtime brings (ADR 0063, ADR 0080): they ship no agent, so they are not "known" in the sense of
#: the CLI listing, the conformance rotation or the image's pinned binaries. `get_backend` has them.
RUNTIME_BACKENDS: frozenset[str] = frozenset({CodeBackend.name, PromptBackend.name})


def known_backends() -> list[str]:
    """The ACP backends shipped in the runner image."""
    return sorted(set(BACKENDS) - RUNTIME_BACKENDS)


# Backend name → **`versions.lock` key**, which is neither the backend's name nor always its
# binary's: `claude-code` is pinned and runs as `claude-agent-acp`, `gemini-cli` is pinned as
# `gemini-cli` and runs as `gemini`.
BACKEND_BINARIES: dict[str, str] = {
    "claude-code": "claude-agent-acp",
    "codex": "codex-acp",
    "gemini-cli": "gemini-cli",
    "goose": "goose",
    "opencode": "opencode",
    "copilot-cli": "copilot-cli",
}


def backend_version(name: str) -> str | None:
    """Version épinglée du binaire qui sert ce backend."""
    return pinned_versions().get(BACKEND_BINARIES.get(name, name))


def pinned_versions() -> dict[str, str]:
    """Versions épinglées des agents installés dans l'image du runner."""
    if not VERSIONS_LOCK.exists():
        return {}
    versions: dict[str, str] = {}
    for raw in VERSIONS_LOCK.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        name, _, version = line.partition("=")
        versions[name.strip()] = version.strip()
    return versions


__all__ = [
    "BACKENDS",
    "BACKEND_BINARIES",
    "RUNTIME_BACKENDS",
    "Backend",
    "ClaudeCodeBackend",
    "CodeBackend",
    "CodexBackend",
    "CopilotCliBackend",
    "GeminiCliBackend",
    "GooseBackend",
    "LaunchPlan",
    "OpenCodeBackend",
    "OpenHandsBackend",
    "backend_version",
    "get_backend",
    "known_backends",
    "pinned_versions",
]
