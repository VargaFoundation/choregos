# SPDX-License-Identifier: Apache-2.0
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
    (r"\brm\s+-rf\s+/(?!\w)", "recursive deletion of the root"),
    (r":\(\)\s*\{\s*:\|:&\s*\};:", "fork bomb"),
    (r"\bcurl\b[^|]*\|\s*(ba)?sh", "running a downloaded script"),
    (r"\bwget\b[^|]*\|\s*(ba)?sh", "running a downloaded script"),
    (r"\bgit\s+push\s+.*--force(?!-with-lease)", "force push"),
    (r"\bgit\s+reset\s+--hard\s+origin", "rewriting the remote history"),
    (r"\bchmod\s+777\b", "permissions too broad"),
    (r"\bhistory\s+-c\b", "erasing traces"),
    (r">\s*/dev/sd[a-z]", "raw disk write"),
)

SECRET_FILE_PATTERNS = (".env", ".git-credentials", "id_rsa", ".npmrc", ".pypirc", ".netrc")


@dataclass(slots=True)
class Decision:
    """Verdict sur une demande de permission, toujours motivé."""

    allowed: bool
    reason: str
    kind: str = "unknown"
    target: str = ""
    #: La nature n'était pas dans la demande ACP : on l'a DÉDUITE du titre ou des arguments.
    #: Dit dans le journal, parce qu'une déduction se relit autrement qu'une déclaration.
    inferred: bool = False

    def to_event(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "kind": self.kind,
            "target": self.target,
            "inferred": self.inferred,
        }


#: Premier mot du titre ACP → nature. Les titres de Claude Code sont « Write /chemin »,
#: « Edit /chemin », « Read /chemin », « Bash: … » ; ceux des autres agents suivent le même
#: réflexe. Ce qu'on ne reconnaît pas reste vide : la règle générale décide alors.
_VERBES_ECRITURE = {
    "write",
    "edit",
    "multiedit",
    "create",
    "delete",
    "remove",
    "move",
    "rename",
    "mkdir",
    "touch",
}
_VERBES_LECTURE = {"read", "glob", "grep", "ls", "search", "view", "cat", "find", "list"}
_VERBES_EXECUTION = {"bash", "run", "exec", "execute", "shell", "sh", "terminal"}
_VERBES_RESEAU = {"webfetch", "fetch", "curl", "http", "websearch", "download"}


def _nature_deduite(title: str, raw: dict[str, Any], *, path: str, command: str, url: str) -> str:
    """Déduit `edit`/`read`/`execute`/`network` d'une demande ACP qui n'a pas de `kind`."""
    if command:
        return "execute"
    if url:
        return "network"
    if any(k in raw for k in ("content", "new_string", "old_string", "edits", "new_str", "text")) and path:
        return "edit"
    premier = title.split(" ", 1)[0].rstrip(":").lower() if title else ""
    if premier in _VERBES_ECRITURE:
        return "edit"
    if premier in _VERBES_LECTURE:
        return "read"
    if premier in _VERBES_EXECUTION:
        return "execute"
    if premier in _VERBES_RESEAU:
        return "network"
    return ""


@dataclass
class GuardRails:
    """Politique de permission appliquée en direct pendant la session ACP."""

    permissions: Permissions
    allowed_paths: list[str] = field(default_factory=list)
    decisions: list[Decision] = field(default_factory=list)
    #: Racine du workspace : les agents demandent des chemins ABSOLUS (`/workspace/src/x.py`),
    #: le périmètre est écrit en relatif (`src/**`). Sans la retirer, tout est « hors
    #: périmètre » — ce que personne n'avait vu, puisque `check_write` ne tournait jamais.
    workspace: str = "/workspace"

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

        inferred = False
        if not kind:
            # Claude Code n'envoie PAS `kind` dans `session/request_permission` : seulement
            # `title` (« Write /workspace/x.py ») et `rawInput`. Sans cette déduction, chaque
            # écriture tombait dans « lecture ou recherche : autorisé » et le périmètre de
            # chemins n'était jamais consulté — 23 écritures sur 23, banc du 2026-09-24.
            kind = _nature_deduite(title, raw, path=path, command=command, url=url)
            inferred = bool(kind)

        if command:
            decision = self.check_command(command)
        elif path and kind in {"edit", "write", "create", "delete", "move"}:
            decision = self.check_write(path)
        elif url:
            decision = self.check_network(url)
        elif kind in {"read", "search", "fetch", "think", "other"}:
            decision = Decision(True, "read or search: allowed", kind, path or title)
        elif not kind and self.permissions.unknown_requests == "reject":
            # Fermé : la politique (`sandbox.unknown_requests: reject`) refuse ce qu'elle ne
            # sait pas nommer. Le message dit à l'agent par où passer — un refus muet fait
            # un agent qui réessaie autrement.
            decision = Decision(
                False,
                "request of unknown kind, refused by the project's policy "
                "(sandbox.unknown_requests: reject); to report something or ask for a wider "
                "scope: `report_finding`, `request_scope_change`",
                "unknown",
                path or title,
            )
        elif not kind:
            decision = Decision(
                True, "unknown kind: allowed and logged (a net, not a wall)", "read", path or title
            )
        else:
            decision = Decision(
                True, f"operation `{kind}` without an identified target: allowed", kind, title
            )
        decision.inferred = inferred
        self.decisions.append(decision)
        return decision

    # ───────────────────────── règles ─────────────────────────

    def check_write(self, path: str) -> Decision:
        normalized = self._relatif(path)
        if any(normalized.endswith(pattern) or pattern in normalized for pattern in SECRET_FILE_PATTERNS):
            return Decision(False, f"writing to a sensitive file is forbidden: {path}", "write", path)
        if normalized == ".choregos/result.json":
            return Decision(True, "writing the step's result: allowed", "write", path)
        if normalized.startswith(".choregos/"):
            return Decision(
                False,
                "the agent cannot change the Choregos configuration "
                "(only `.choregos/result.json` is expected)",
                "write",
                path,
            )
        if not self.allowed_paths:
            return Decision(True, "no scope declared: write allowed", "write", path)
        if matches_any(normalized, self.allowed_paths):
            return Decision(True, "within the allowed paths", "write", path)
        return Decision(
            False,
            f"`{path}` is outside the allowed paths. Use `report_finding` if it is another "
            "problem, or `request_scope_change` if it is essential to this work item.",
            "write",
            path,
        )

    def _relatif(self, path: str) -> str:
        """Le chemin tel que le périmètre le nomme : relatif à la racine du workspace."""
        racine = self.workspace.rstrip("/")
        if racine and (path == racine or path.startswith(racine + "/")):
            path = path[len(racine) :]
        return path.removeprefix("./").lstrip("/")  # `lstrip(".")` mangerait `.choregos`

    def check_command(self, command: str) -> Decision:
        lowered = command.lower()
        for pattern, why in DANGEROUS_PATTERNS:
            if re.search(pattern, lowered):
                return Decision(False, f"command refused ({why})", "execute", command[:200])
        for denied in self.permissions.deny_commands:
            if _command_matches(lowered, denied.lower()):
                return Decision(
                    False, f"command forbidden by the policy: `{denied}`", "execute", command[:200]
                )
        return Decision(True, "command allowed", "execute", command[:200])

    def check_network(self, url: str) -> Decision:
        if not self.permissions.allow_domains:
            return Decision(True, "no network allowlist: allowed (the sandbox filters)", "network", url)
        host = _host_of(url)
        if any(host == domain or host.endswith(f".{domain}") for domain in self.permissions.allow_domains):
            return Decision(True, f"allowed domain: {host}", "network", url)
        return Decision(
            False,
            f"domain not allowed: {host} (allowlist: {', '.join(self.permissions.allow_domains)})",
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
