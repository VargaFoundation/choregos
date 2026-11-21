# SPDX-License-Identifier: Apache-2.0
"""What a cached input token costs, as a multiple of the model's input price (S4-12, ADR 0200).

The gateway prices a request itself, with the provider's own multipliers, and that figure is what the
ledger books. Only an organisation's `input_per_1k_override` makes the platform compute a price: it
used to apply one rate to every input token, cache reads and writes included. This module holds the
rest of the rule: the multipliers, their defaults per provider family, and the price of the input.

The defaults are the providers' list-price ratios; a model that differs says so in its catalogue
entry, which wins:

- **Anthropic** (prompt caching): a read costs 0.1x the input price, a 5-minute write 1.25x, a 1-hour
  write 2x. Claude Opus 5.5 and Sonnet 5.5 read at 0.05x (as the story that introduced this module
  states it; not re-checked against a live price list).
- **OpenAI / Azure OpenAI**: reads are discounted (0.5x on the GPT-4o family, 0.25x on 4.1, 0.1x on
  5); writes are free of charge (1x). The default is the dearest, 0.5x: a default must not under-state
  a cost. The gateway's own `cached_input_cost_per_1k` replaces it when it is known.
- **Gemini / Vertex**: an implicit-cache read costs 0.25x (0.1x on 2.5); the write is billed as
  storage, not per token (1x here). The default is the dearer, 0.25x.
- **Anything else**: 1x everywhere, which is what the override did before this module: never less than
  a fresh token.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Final

from .prompt_cache import anthropic_family

_OPENAI_MARKERS: Final = ("openai", "azure", "gpt-")
_GEMINI_MARKERS: Final = ("gemini", "vertex_ai")
#: Opus 5.5 and Sonnet 5.5, however the gateway spells them (`claude-opus-5-5`, `claude-sonnet-5.5`).
_HALF_PRICE_READ: Final = re.compile(r"(opus|sonnet)[-_.]?5[-_.]5")


@dataclass(frozen=True)
class CacheMultipliers:
    read: float
    write_5m: float
    write_1h: float


UNIT: Final = CacheMultipliers(1.0, 1.0, 1.0)
ANTHROPIC: Final = CacheMultipliers(0.1, 1.25, 2.0)
ANTHROPIC_HALF_READ: Final = CacheMultipliers(0.05, 1.25, 2.0)
OPENAI: Final = CacheMultipliers(0.5, 1.0, 1.0)
GEMINI: Final = CacheMultipliers(0.25, 1.0, 1.0)


def family_defaults(model_name: str, provider: str = "", upstream_model: str = "") -> CacheMultipliers:
    """The default multipliers of the model's provider family, told from its names."""
    named = [n for n in (model_name, provider, upstream_model) if n]
    names = " ".join(named).lower()
    if "anthropic" in names or any(anthropic_family(n) for n in named):
        return ANTHROPIC_HALF_READ if _HALF_PRICE_READ.search(names) else ANTHROPIC
    if any(marker in names for marker in _GEMINI_MARKERS):
        return GEMINI
    if any(marker in names for marker in _OPENAI_MARKERS):
        return OPENAI
    return UNIT


@dataclass(frozen=True)
class ResolvedMultipliers:
    multipliers: CacheMultipliers
    #: Where the read multiplier comes from: the entry's own, the gateway's cached-input price, or
    #: the family's default. The writes come from the entry or the family.
    source: str


def resolve(
    *,
    model_name: str,
    provider: str = "",
    upstream_model: str = "",
    read: float | None = None,
    write_5m: float | None = None,
    write_1h: float | None = None,
    input_cost_per_1k: float | None = None,
    cached_input_cost_per_1k: float | None = None,
) -> ResolvedMultipliers:
    """The entry's own multipliers, else (read only) the ratio of the gateway's cached-input price to
    its input price, else the family's default."""
    base = family_defaults(model_name, provider, upstream_model)
    source = "family_default"
    if read is not None:
        source = "override"
    elif input_cost_per_1k and cached_input_cost_per_1k is not None:
        read, source = cached_input_cost_per_1k / input_cost_per_1k, "gateway"
    return ResolvedMultipliers(
        replace(
            base,
            read=base.read if read is None else read,
            write_5m=base.write_5m if write_5m is None else write_5m,
            write_1h=base.write_1h if write_1h is None else write_1h,
        ),
        source,
    )


def input_cost_usd(
    *,
    price_per_1k: float,
    tokens_in: int,
    tokens_cached: int,
    tokens_cache_write: int,
    tokens_cache_write_1h: int,
    multipliers: CacheMultipliers,
) -> float:
    """What `tokens_in` cost at `price_per_1k`, each kind at its own rate. `tokens_cached` and
    `tokens_cache_write` are parts of `tokens_in`, and `tokens_cache_write_1h` a part of the writes
    (the rest is the 5-minute write); a count that exceeds its whole is clamped, never negative."""
    reads = max(min(tokens_cached, tokens_in), 0)
    writes = max(min(tokens_cache_write, tokens_in - reads), 0)
    long_writes = max(min(tokens_cache_write_1h, writes), 0)
    fresh = tokens_in - reads - writes
    weighted = (
        fresh
        + reads * multipliers.read
        + (writes - long_writes) * multipliers.write_5m
        + long_writes * multipliers.write_1h
    )
    return weighted / 1000 * price_per_1k
