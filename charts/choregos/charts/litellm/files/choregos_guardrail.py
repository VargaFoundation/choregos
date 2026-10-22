# SPDX-License-Identifier: Apache-2.0
"""The guardrail on the LLM path: secrets masked in what leaves the gateway for a provider (ADR 0084).

An agent sends its model everything it read: a file holding a key, a log line with a bearer token,
a clone URL with credentials. The agent process cannot be trusted to clean that up — it is the
thing being guarded — so the masking happens at the gateway, in LiteLLM, before the provider call.

The gateway loads this file as a custom guardrail (`charts/choregos/charts/litellm/files/`, a
byte-identical copy, and the local install writes it next to LiteLLM's config). It runs on every
request but acts only for a key whose **metadata** says `choregos_llm_guard: redact`, which the
platform writes when it mints a run's key and the policy sets `sandbox.llm_security_analyzer`. A
key's metadata is the platform's, not the agent's: a request's own `metadata` is never read.

Only high-precision shapes are masked, because the model sees what is left and may write it back:
known token shapes (the `no_secrets` gate's), a long bearer or basic credential, and literal URL
user-info. `password = getpass()` stays code. The model sees `[REDACTED]` instead of the value; an
edit whose old text held that value fails, which is the price of the value never reaching the
provider. A real scanner does more; this one would rather miss than shred a prompt.

Stdlib only: LiteLLM's image has no Choregos package. When LiteLLM is importable the class is a
`CustomGuardrail`; otherwise a plain class the tests drive the same way.
"""

from __future__ import annotations

import importlib
import logging
import re
from collections.abc import Callable
from typing import Any

#: The key metadata entry the guard reads, and the one value that turns it on.
GUARD_METADATA_KEY = "choregos_llm_guard"
GUARD_REDACT = "redact"

#: What a masked value becomes (the same marker as `choregos_core.redaction`).
REDACTED = "[REDACTED]"

#: The `no_secrets` gate's shapes (`choregos_core.gates.SECRET_PATTERNS`); a test keeps them equal.
KNOWN_SHAPES: tuple[str, ...] = (
    r"AKIA[0-9A-Z]{16}",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"gh[pousr]_[A-Za-z0-9]{20,}",
    r"sk-ant-[A-Za-z0-9_-]{20,}",
    r"sk-(?:proj-)?[A-Za-z0-9]{32,}",
    r"xox[baprs]-[A-Za-z0-9-]{10,}",
    r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
)
_SHAPES = [re.compile(pattern) for pattern in KNOWN_SHAPES]

#: `Bearer <16+ chars with a digit>`: "a basic example" and "bearer tokens" stay prose.
_SCHEME_CREDENTIALS = re.compile(r"(?i)\b(bearer|basic)\s+(?=[A-Za-z0-9._~+/=-]*\d)[A-Za-z0-9._~+/=-]{16,}")

#: `scheme://user:password@` with a literal password: `{password}` or `$(PASSWORD)` stays a template.
_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[A-Za-z0-9._~%!*+-]+:[A-Za-z0-9._~%!*+-]+@")

#: The request fields a provider reads as text, across the OpenAI and Anthropic shapes.
_TEXT_FIELDS = ("messages", "system", "input", "prompt", "instructions")

#: Content blocks that carry media, not text: base64 is left alone, by block type, never by key
#: name — a tool call's `data` argument is text the model wrote.
_MEDIA_TYPES = frozenset(
    {"image", "image_url", "input_image", "document", "file", "input_file", "input_audio", "audio"}
)


def mask(text: str) -> tuple[str, int]:
    """`text` with every high-precision secret replaced by `REDACTED`, and how many were."""
    count = 0

    def _counted(replacement: str) -> Callable[[re.Match[str]], str]:
        def replace(match: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return match.expand(replacement)

        return replace

    masked = _SCHEME_CREDENTIALS.sub(_counted(rf"\1 {REDACTED}"), text)
    masked = _URL_USERINFO.sub(_counted(rf"\1{REDACTED}@"), masked)
    for shape in _SHAPES:
        masked = shape.sub(_counted(REDACTED), masked)
    return masked, count


def _walk(value: Any) -> tuple[Any, int]:
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, list):
        total = 0
        items = []
        for item in value:
            masked, count = _walk(item)
            items.append(masked)
            total += count
        return items, total
    if isinstance(value, dict):
        if value.get("type") in _MEDIA_TYPES:
            return value, 0
        total = 0
        out: dict[Any, Any] = {}
        for key, item in value.items():
            out[key], count = _walk(item)
            total += count
        return out, total
    return value, 0


def guard_request(data: dict[str, Any]) -> int:
    """Masks the text fields of a provider request in place; returns how many secrets it masked."""
    total = 0
    for field in _TEXT_FIELDS:
        if field in data:
            data[field], count = _walk(data[field])
            total += count
    return total


def is_on(key_metadata: Any) -> bool:
    """Whether the key's metadata — the platform's, set at mint time — asks for the guard."""
    return isinstance(key_metadata, dict) and key_metadata.get(GUARD_METADATA_KEY) == GUARD_REDACT


_logger = logging.getLogger("choregos.llm_guard")


def _base() -> type:
    try:
        module = importlib.import_module("litellm.integrations.custom_guardrail")
    except ImportError:
        return object
    base: type = module.CustomGuardrail
    return base


_Base: Any = _base()


class ChoregosGuardrail(_Base):  # type: ignore[misc]
    """LiteLLM's `pre_call` guardrail: config `guardrail: choregos_guardrail.ChoregosGuardrail`.

    Fails closed: an error here is LiteLLM's error for the request, which never reaches the provider.
    """

    def __init__(self, **kwargs: Any) -> None:
        if _Base is not object:
            super().__init__(**kwargs)

    async def async_pre_call_hook(
        self, user_api_key_dict: Any, cache: Any, data: dict[str, Any], call_type: Any
    ) -> Any:
        if not is_on(getattr(user_api_key_dict, "metadata", None)):
            return data
        masked = guard_request(data)
        if masked:
            # A count, never the values. Not written into the request: `metadata` reaches the provider.
            _logger.info("choregos llm guard masked %d secret(s)", masked)
        return data


__all__ = [
    "GUARD_METADATA_KEY",
    "GUARD_REDACT",
    "KNOWN_SHAPES",
    "REDACTED",
    "ChoregosGuardrail",
    "guard_request",
    "is_on",
    "mask",
]
