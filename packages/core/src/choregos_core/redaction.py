# SPDX-License-Identifier: Apache-2.0
"""Masking secrets in text that leaves its process: pod logs, executor messages (S36-02).

A runner's pod log can hold anything the agent printed — an environment dump, a failed `curl`
with its `Authorization` header, a clone URL with credentials. Logs are archived and shown on
the run page, so every line goes through `redact` first.

Deliberately simple and conservative: it masks the known token shapes (the same ones the
`no_secrets` gate looks for), bearer and basic credentials, URL user-info, and the value of any
`key=value` / `key: value` whose key names a secret. It complements a real scanner; it does not
replace one, and it would rather mask too much than too little.
"""

from __future__ import annotations

import re

from .gates import SECRET_PATTERNS

#: What a masked value becomes.
REDACTED = "[REDACTED]"

_KNOWN_SHAPES = [re.compile(pattern) for _, pattern in SECRET_PATTERNS]

#: `Authorization: Bearer xyz`, `curl -H "Bearer xyz"`.
_SCHEME_CREDENTIALS = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{6,}")

#: `https://user:password@host` — the user-info of a URL.
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@")

#: `GITHUB_TOKEN=…`, `"client_secret": "…"`, `password: …` — the key ENDS with a secret word, so
#: `tokens_in: 1200` stays readable. A `Bearer`/`Basic` prefix stays visible, its value does not.
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b([A-Za-z0-9_.-]*(?:token|secret|password|passwd|pwd|api[_-]?key|access[_-]?key"
    r"|private[_-]?key|authorization|credentials?))"
    r"([\"']?\s*[=:]\s*[\"']?(?:(?:bearer|basic)\s+)?)"
    r"([^\s\"'&,;]+)"
)


def redact(text: str) -> str:
    """`text` with every secret it visibly holds replaced by `REDACTED`."""
    if not text:
        return text
    masked = _SECRET_ASSIGNMENT.sub(
        lambda m: m.group(0) if m.group(3) == REDACTED else f"{m.group(1)}{m.group(2)}{REDACTED}", text
    )
    masked = _SCHEME_CREDENTIALS.sub(lambda m: f"{m.group(1)} {REDACTED}", masked)
    masked = _URL_USERINFO.sub(lambda m: f"{m.group(1)}{REDACTED}@", masked)
    for shape in _KNOWN_SHAPES:
        masked = shape.sub(REDACTED, masked)
    return masked


__all__ = ["REDACTED", "redact"]
