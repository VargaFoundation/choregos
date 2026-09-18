"""Garde-fous du runner : ce que l'agent a le droit de faire, et ce qui est vérifié après.

La permission n'est pas la garantie : la garantie, c'est la vérification du diff et les
gates. Ici on refuse ce qui est refusable tout de suite, et on journalise tout.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any

from choregos_contracts import Permissions
from choregos_core import matches_any

DANGEROUS_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\brm\s+-rf\s+/(?!\w)", "suppression récursive de la racine"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    (r"\bcurl\b[^|]*\|\s*(ba)?sh", "exécution d'un script téléchargé"),
    (r"\bwget\b[^|]*\|\s*(ba)?sh", "exécution d'un script téléchargé"),
    (r"\bgit\s+push\s+.*--force(?!-with-lease)", "push forcé"),
    (r"\bgit\s+reset\s+--hard\s+origin", "réécriture de l'historique distant"),
    (r"\bchmod\s+777\b", "permissions trop larges"),
    (r"\bhistory\s+-c\b", "effacement de traces"),
    (r">\s*/dev/sd[a-z]", "écriture disque brute"),
)

SECRET_FILE_PATTERNS = (".env", ".git-credentials", "id_rsa", ".npmrc", ".pypirc", ".netrc")


@dataclass(slots=True)
class Decision:
    """Verdict sur une demande de permission, toujours motivé."""

    allowed: bool
    reason: str
    kind: str = "unknown"
    target: str = ""

    def to_event(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "kind": self.kind,
            "target": self.target,
        }


@dataclass
class GuardRails:
    """Politique de permission appliquée en direct pendant la session ACP."""

    permissions: Permissions
    allowed_paths: list[str] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.allowed_paths:
            self.allowed_paths = list(self.permissions.write_paths)

    # ───────────────────────── entrée principale ─────────────────────────

    def decide(self, params: dict[str, Any]) -> Decision:
        """Analyse une demande ACP `session/request_permission` et tranche."""
        tool = params.get("toolCall", params.get("tool_call", params)) or {}
        kind = str(tool.get("kind") or tool.get("type") or "").lower()
        raw = tool.get("rawInput") or tool.get("input") or {}
        title = str(tool.get("title") or "")

        command = _first_str(raw, ("command", "cmd", "script", "shell"))
        path = _first_str(raw, ("path", "file", "filePath", "file_path", "abs_path"))
        url = _first_str(raw, ("url", "endpoint"))

        if command:
            decision = self.check_command(command)
        elif path and kind in {"edit", "write", "create", "delete", "move"}:
            decision = self.check_write(path)
        elif url:
            decision = self.check_network(url)
        elif kind in {"read", "search", "fetch", "think", "other", ""}:
            decision = Decision(True, "lecture ou recherche : autorisé", kind or "read", path or title)
        else:
            decision = Decision(True, f"opération `{kind}` sans cible identifiée : autorisée", kind, title)
        self.decisions.append(decision)
        return decision

    # ───────────────────────── règles ─────────────────────────

    def check_write(self, path: str) -> Decision:
        normalized = path.removeprefix("./").lstrip("/")  # `lstrip(".")` mangerait `.choregos`
        if any(normalized.endswith(pattern) or pattern in normalized for pattern in SECRET_FILE_PATTERNS):
            return Decision(False, f"écriture interdite dans un fichier sensible : {path}", "write", path)
        if normalized == ".choregos/result.json":
            return Decision(True, "dépôt du résultat de l'étape : autorisé", "write", path)
        if normalized.startswith(".choregos/"):
            return Decision(
                False,
                "la configuration Choregos n'est pas modifiable par l'agent "
                "(seul `.choregos/result.json` est attendu)",
                "write",
                path,
            )
        if not self.allowed_paths:
            return Decision(True, "aucun périmètre déclaré : écriture autorisée", "write", path)
        if matches_any(normalized, self.allowed_paths):
            return Decision(True, "dans le périmètre autorisé", "write", path)
        return Decision(
            False,
            f"`{path}` est hors du périmètre autorisé. Utilise `report_finding` si c'est un autre "
            "problème, ou `request_scope_change` si c'est indispensable à ce ticket.",
            "write",
            path,
        )

    def check_command(self, command: str) -> Decision:
        lowered = command.lower()
        for pattern, why in DANGEROUS_PATTERNS:
            if re.search(pattern, lowered):
                return Decision(False, f"commande refusée ({why})", "execute", command[:200])
        for denied in self.permissions.deny_commands:
            if _command_matches(lowered, denied.lower()):
                return Decision(
                    False, f"commande interdite par la politique : `{denied}`", "execute", command[:200]
                )
        return Decision(True, "commande autorisée", "execute", command[:200])

    def check_network(self, url: str) -> Decision:
        if not self.permissions.allow_domains:
            return Decision(True, "aucune allowlist réseau : autorisé (le sandbox filtre)", "network", url)
        host = _host_of(url)
        if any(host == domain or host.endswith(f".{domain}") for domain in self.permissions.allow_domains):
            return Decision(True, f"domaine autorisé : {host}", "network", url)
        return Decision(
            False,
            f"domaine non autorisé : {host} (allowlist : {', '.join(self.permissions.allow_domains)})",
            "network",
            url,
        )

    def extend_scope(self, paths: list[str]) -> None:
        """Élargit le périmètre à chaud après un `request_scope_change` accordé."""
        for path in paths:
            if path not in self.allowed_paths:
                self.allowed_paths.append(path)

    @property
    def denials(self) -> int:
        return sum(1 for decision in self.decisions if not decision.allowed)


def _first_str(payload: Any, keys: tuple[str, ...]) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, list) and value and isinstance(value[0], str):
            return " ".join(value)
    return ""


def _command_matches(command: str, denied: str) -> bool:
    """`kubectl` interdit `kubectl get pods` ; `terraform apply` n'interdit pas `terraform plan`."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    denied_tokens = denied.split()
    if not denied_tokens:
        return False
    for index in range(len(tokens) - len(denied_tokens) + 1):
        if [t.rsplit("/", 1)[-1] for t in tokens[index : index + len(denied_tokens)]] == denied_tokens:
            return True
    return False


def _host_of(url: str) -> str:
    from urllib.parse import urlparse

    parsed = urlparse(url if "://" in url else f"https://{url}")
    return (parsed.hostname or "").lower()
