# SPDX-License-Identifier: Apache-2.0
"""The supply-chain gate of a skill version (S32-06, ADR 0050).

Every version is assessed when it is published — from a zip, the console, git or a template:

- a **risk tier**: `low` (a `SKILL.md` alone), `medium` (references, assets: other text files),
  `high` (a script, an executable bit, an outbound URL, a credential-like string, an MCP reference,
  or any critical finding of the scan);
- a **static scan**: prompt-injection phrases, hidden or bidirectional Unicode, base64 or
  hex-encoded blobs, a download piped into a shell;
- a **licence**: an open licence passes; a missing or non-open one is flagged — it does not raise
  the risk, it tells the reviewer what they are taking on (several skills of `anthropics/skills` are
  source-available, not open source).

A `high` version waits for an administrator who is not its importer (`pending_review`); until
then no agent may pin it and no run receives it.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from choregos_contracts import is_inert_binary_path

from .skill_sources import detect_licence

Risk = Literal["low", "medium", "high"]
Severity = Literal["info", "warning", "critical"]
RANK: dict[str, int] = {"low": 0, "medium": 1, "high": 2}

#: Licences a skill may carry without a flag (SPDX identifiers).
OPEN_LICENCES = frozenset(
    {
        "0BSD",
        "AGPL-3.0",
        "Apache-2.0",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "CC-BY-4.0",
        "CC-BY-SA-4.0",
        "CC0-1.0",
        "GPL-2.0",
        "GPL-3.0",
        "ISC",
        "LGPL-2.1",
        "LGPL-3.0",
        "MIT",
        "MPL-2.0",
        "Unlicense",
    }
)

SCRIPT_EXTENSIONS = frozenset(
    {
        ".sh",
        ".bash",
        ".zsh",
        ".py",
        ".js",
        ".mjs",
        ".cjs",
        ".ts",
        ".rb",
        ".pl",
        ".php",
        ".ps1",
        ".bat",
        ".cmd",
        ".lua",
        ".go",
        ".rs",
        ".java",
        ".exe",
        ".dll",
        ".so",
        ".jar",
        ".wasm",
    }
)

URL = re.compile(r"\bhttps?://[^\s<>\"'`)\]]+", re.IGNORECASE)
_CREDENTIALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("an AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{40,})\b")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("an API key", re.compile(r"\bsk-(ant-|proj-)?[A-Za-z0-9_-]{20,}\b")),
    ("a private key", re.compile(r"-----BEGIN ([A-Z]+ )?PRIVATE KEY-----")),
    (
        "a secret assigned in clear",
        re.compile(
            r"\b(password|passwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)\b\s*[:=]\s*"
            r"[\"'][^\"'\s]{8,}[\"']",
            re.IGNORECASE,
        ),
    ),
)
MCP = re.compile(r"\bmcp__\w+|\bmcpServers\b|\.mcp\.json\b|\bmodelcontextprotocol\b|\bmcp[_-]?server\b", re.I)
_INJECTIONS = re.compile(
    r"ignore\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|earlier|preceding)\s+(instructions|prompts?|rules)"
    r"|disregard\s+(all\s+|any\s+)?(the\s+)?(previous|prior|above|system)\s+(instructions|prompts?|rules)"
    r"|forget\s+(all\s+)?(your|the)\s+(previous\s+)?instructions"
    r"|do\s+not\s+(tell|inform|reveal\s+to)\s+the\s+user"
    r"|without\s+(telling|informing)\s+the\s+user"
    r"|(reveal|print|output|leak)\s+(your|the)\s+system\s+prompt"
    r"|you\s+are\s+now\s+(in\s+)?(developer|dan|jailbreak|unrestricted)",
    re.IGNORECASE,
)
#: Zero-width, bidirectional controls, word joiners, the BOM inside a text, and Unicode tags.
_HIDDEN = re.compile(r"[\u200b-\u200f\u202a-\u202e\u2060-\u2064\u2066-\u2069\ufeff\U000e0000-\U000e007f]")
BOM = "\ufeff"
_BASE64 = re.compile(r"[A-Za-z0-9+/]{160,}={0,2}")
_HEX = re.compile(r"(\\x[0-9a-fA-F]{2}){24,}|\b[0-9a-fA-F]{200,}\b")
_DECODE_EXEC = re.compile(
    r"(eval|exec)\s*\(\s*(atob|base64\.b64decode|bytes\.fromhex|codecs\.decode|Buffer\.from)"
    r"|base64\s+(-d|--decode)[^\n|]*\|\s*(ba|z|da)?sh\b",
    re.IGNORECASE,
)
_PIPE_TO_SHELL = re.compile(
    r"\b(curl|wget|fetch)\b[^\n|;]*\|\s*(sudo\s+)?(ba|z|da|k)?sh\b"
    r"|\b(iwr|irm|Invoke-WebRequest|Invoke-RestMethod)\b[^\n]*\|\s*(iex|Invoke-Expression)\b"
    r"|\bbash\s+<\(\s*curl",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Finding:
    code: str
    severity: Severity
    detail: str
    file: str | None = None
    line: int | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "file": self.file,
            "line": self.line,
        }


@dataclass
class Assessment:
    risk: Risk
    findings: list[Finding] = field(default_factory=list)
    licence: str | None = None

    @property
    def review_status(self) -> str:
        return "pending_review" if self.risk == "high" else "approved"

    def findings_as_dicts(self) -> list[dict[str, object]]:
        return [finding.as_dict() for finding in self.findings]


def _line(texte: str, position: int) -> int:
    return texte.count("\n", 0, position) + 1


def _is_script(chemin: str, texte: str) -> bool:
    _, extension = posixpath.splitext(chemin.lower())
    return extension in SCRIPT_EXTENSIONS or texte.startswith("#!") or "scripts" in chemin.split("/")[:-1]


def _scan_file(chemin: str, texte: str) -> list[Finding]:
    """What a single file says that a reviewer must read."""
    trouves: list[Finding] = []
    if _is_script(chemin, texte):
        trouves.append(Finding("script", "warning", "an executable script ships with the skill", chemin))
    hotes: set[str] = set()
    for lien in URL.finditer(texte):
        hote = re.sub(r"^https?://", "", lien.group(0), flags=re.I).split("/", 1)[0].lower()
        if hote not in hotes:
            hotes.add(hote)
            trouves.append(
                Finding(
                    "outbound_url",
                    "warning",
                    f"an outbound URL to {hote}",
                    chemin,
                    _line(texte, lien.start()),
                )
            )
    for quoi, motif in _CREDENTIALS:
        if trouve := motif.search(texte):
            trouves.append(
                Finding(
                    "credential", "critical", f"what looks like {quoi}", chemin, _line(texte, trouve.start())
                )
            )
    if trouve := MCP.search(texte):
        trouves.append(
            Finding(
                "mcp_reference",
                "warning",
                f"an MCP reference: `{trouve.group(0)}`",
                chemin,
                _line(texte, trouve.start()),
            )
        )
    if trouve := _INJECTIONS.search(texte):
        trouves.append(
            Finding(
                "prompt_injection",
                "critical",
                f"a prompt-injection phrase: “{trouve.group(0)}”",
                chemin,
                _line(texte, trouve.start()),
            )
        )
    cache = _HIDDEN.search(texte[1:] if texte.startswith(BOM) else texte)
    if cache:
        trouves.append(
            Finding(
                "hidden_unicode",
                "critical",
                f"an invisible or bidirectional character U+{ord(cache.group(0)):04X}",
                chemin,
                _line(texte, cache.start()),
            )
        )
    for motif in (_BASE64, _HEX, _DECODE_EXEC):
        if trouve := motif.search(texte):
            trouves.append(
                Finding(
                    "obfuscated",
                    "critical",
                    "an encoded blob, or code that decodes then runs it",
                    chemin,
                    _line(texte, trouve.start()),
                )
            )
            break
    if trouve := _PIPE_TO_SHELL.search(texte):
        trouves.append(
            Finding(
                "pipe_to_shell",
                "critical",
                "a download piped into a shell",
                chemin,
                _line(texte, trouve.start()),
            )
        )
    return trouves


def _licence_findings(licence: str | None) -> list[Finding]:
    if licence is None:
        return [Finding("licence_missing", "warning", "no licence found: the right to use it is unknown")]
    if licence not in OPEN_LICENCES:
        return [Finding("licence_not_open", "warning", f"`{licence}` is not an open-source licence")]
    return []


#: The findings that rate a version `high`, whatever their severity; a critical one always does.
HIGH_CODES = frozenset({"script", "executable", "outbound_url", "mcp_reference"})


def raises_to_high(finding: Finding) -> bool:
    return finding.code in HIGH_CODES or finding.severity == "critical"


def assess(
    files: dict[str, str],
    *,
    executables: list[str] | tuple[str, ...] = (),
    repo_licences: dict[str, str] | None = None,
) -> Assessment:
    """The risk tier, the scan's findings and the licence of a version's files."""
    trouves: list[Finding] = []
    for chemin in sorted(files):
        if is_inert_binary_path(chemin):
            continue  # base64 of a font or an image: nothing a text scan could read
        trouves.extend(_scan_file(chemin, files[chemin]))
    for chemin in sorted(set(executables)):
        trouves.append(Finding("executable", "warning", "a file with an executable bit", chemin))
    risque: Risk = "medium" if set(files) - {"SKILL.md"} else "low"
    if any(raises_to_high(f) for f in trouves):
        risque = "high"
    licence = detect_licence(files, repo_licences)
    trouves.extend(_licence_findings(licence))
    return Assessment(risk=risque, findings=trouves, licence=licence)


#: Who approved a version shipped by the platform itself (its agent catalogue, a template).
PLATFORM = "platform"


def columns(assessment: Assessment) -> dict[str, Any]:
    """The `skill_versions` columns of an assessed version imported by a person."""
    return {
        "risk": assessment.risk,
        "findings": assessment.findings_as_dicts(),
        "review_status": assessment.review_status,
    }


def platform_columns(files: dict[str, str]) -> dict[str, Any]:
    """A version the platform ships (its catalogue, a template): assessed and recorded like any
    other, approved by the platform — the review of what ships is the review of the release."""
    return {**columns(assess(files)), "review_status": "approved", "reviewed_by": PLATFORM}


__all__ = [
    "HIGH_CODES",
    "OPEN_LICENCES",
    "PLATFORM",
    "RANK",
    "Assessment",
    "Finding",
    "assess",
    "columns",
    "platform_columns",
    "raises_to_high",
]
