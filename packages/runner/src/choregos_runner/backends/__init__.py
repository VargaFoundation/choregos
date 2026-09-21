"""Registre des backends ACP livrés."""

from __future__ import annotations

from pathlib import Path

from .base import Backend, LaunchPlan
from .claude_code import ClaudeCodeBackend
from .others import (
    CodexBackend,
    CopilotCliBackend,
    GeminiCliBackend,
    GooseBackend,
    OpenCodeBackend,
)

BACKENDS: dict[str, type[Backend]] = {
    ClaudeCodeBackend.name: ClaudeCodeBackend,
    CodexBackend.name: CodexBackend,
    GeminiCliBackend.name: GeminiCliBackend,
    GooseBackend.name: GooseBackend,
    OpenCodeBackend.name: OpenCodeBackend,
    CopilotCliBackend.name: CopilotCliBackend,
}

# Backends retirés : un nom qu'on reconnaît, pour refuser avec la raison plutôt qu'avec un
# « backend inconnu » qui laisserait croire à une faute de frappe.
RETIRED: dict[str, str] = {
    "openhands": (
        "retiré le 2026-09-21 : OpenHands n'expose aucun agent ACP en ligne de commande — ni en 0.59 "
        "(`serve` et `cli` seulement), ni en 1.x (plus de binaire, un serveur HTTP `agent-server`). "
        "Voir docs/adr/0011-retrait-d-openhands.md ; le défaut est désormais `claude-code`."
    ),
}

VERSIONS_LOCK = Path(__file__).parent / "versions.lock"


def get_backend(name: str) -> Backend:
    """Instancie un backend par son nom ; une erreur explicite si le nom est inconnu."""
    if name in RETIRED:
        raise KeyError(f"backend {name} {RETIRED[name]}")
    backend_class = BACKENDS.get(name)
    if backend_class is None:
        raise KeyError(f"backend inconnu : {name} (connus : {', '.join(sorted(BACKENDS))})")
    return backend_class()


def known_backends() -> list[str]:
    return sorted(BACKENDS)


# Nom du backend → **clé de `versions.lock`**, qui n'est ni le nom du backend ni celui du
# binaire : `claude-code` est épinglé sous `claude-agent-acp` et s'exécute en
# `claude-code-acp`, `gemini-cli` est épinglé sous `gemini-cli` et s'exécute en `gemini`.
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
    "Backend",
    "ClaudeCodeBackend",
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
