# SPDX-License-Identifier: Apache-2.0
"""What a voice session spends, as its relay measures it (S41-15, ADR 0080 §10).

A gateway's count of a realtime socket is not a figure to book: LiteLLM prices what it relays
over `/realtime` after the fact, when it does at all, and audio tokens cost several times text
ones. So the relay prices each `response.done` itself, from the usage the model reports and the
model's own prices: text, audio and cached input, text and audio output. The session closes the
moment the sum reaches its cap, and that sum is what the ledger books; the gateway's figure is
kept next to it, for comparison only.

The prices are the organisation's price override when it sets one (it then prices every token,
audio or not, as for any other model), else the gateway's. A realtime model the gateway gives no
audio price for, and the organisation none either, opens no voice session: an unpriced token is a
spend no cap can stop.

The cap is the session's reservation, and, when the catalogue gives the model a
`max_usd_per_minute`, that rate for each minute the session has lasted, the first one counted
whole.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any, Literal

from choregos_core import GatewayModel, Spend

from ..db.models import ModelCatalogueRow


@dataclass(frozen=True)
class RealtimePrices:
    """USD per 1,000 tokens of each kind a realtime response reports."""

    text_in: float
    text_out: float
    audio_in: float
    audio_out: float
    cached_in: float
    priced_by: Literal["gateway", "override"]

    def as_payload(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> RealtimePrices:
        return cls(
            text_in=float(payload["text_in"]),
            text_out=float(payload["text_out"]),
            audio_in=float(payload["audio_in"]),
            audio_out=float(payload["audio_out"]),
            cached_in=float(payload["cached_in"]),
            priced_by="override" if payload.get("priced_by") == "override" else "gateway",
        )


def realtime_prices(model: GatewayModel, row: ModelCatalogueRow | None) -> RealtimePrices | None:
    """The prices a voice session of `model` is measured at, or `None` when it has none to be."""
    if row is not None and row.input_per_1k_override is not None and row.output_per_1k_override is not None:
        price_in, price_out = row.input_per_1k_override, row.output_per_1k_override
        return RealtimePrices(price_in, price_out, price_in, price_out, price_in, "override")
    known = (
        model.input_cost_per_1k,
        model.output_cost_per_1k,
        model.input_audio_cost_per_1k,
        model.output_audio_cost_per_1k,
    )
    if any(price is None for price in known):
        return None
    text_in, text_out, audio_in, audio_out = (float(price or 0.0) for price in known)
    # Without a cached price, a cached token costs what a fresh one does: never less than it is.
    cached = model.cached_input_cost_per_1k
    return RealtimePrices(
        text_in, text_out, audio_in, audio_out, text_in if cached is None else cached, "gateway"
    )


@dataclass(frozen=True)
class ResponseCost:
    cost_usd: float
    tokens_in: int
    tokens_out: int
    tokens_cached: int


def _count(details: Mapping[str, Any], key: str) -> int | None:
    value = details.get(key)
    return int(value) if isinstance(value, int | float) and value >= 0 else None


def response_cost(usage: Mapping[str, Any], prices: RealtimePrices) -> ResponseCost:
    """The cost of one response from its `usage`, as the Realtime API reports it.

    `input_tokens` counts the cached ones; `input_token_details` splits it in text and audio, and
    `cached_tokens_details` the cached part. A total without its split is priced at the dearer of
    its two kinds: a model that reports less is never measured cheaper than it may cost."""
    tokens_in = _count(usage, "input_tokens") or 0
    tokens_out = _count(usage, "output_tokens") or 0
    in_details: Mapping[str, Any] = usage.get("input_token_details") or {}
    out_details: Mapping[str, Any] = usage.get("output_token_details") or {}

    audio_in = _count(in_details, "audio_tokens")
    text_in = _count(in_details, "text_tokens")
    if audio_in is None and text_in is None:
        audio_in, text_in = (tokens_in, 0) if prices.audio_in >= prices.text_in else (0, tokens_in)
    elif audio_in is None:
        audio_in = max(tokens_in - (text_in or 0), 0)
    elif text_in is None:
        text_in = max(tokens_in - audio_in, 0)
    assert text_in is not None

    cached = _count(in_details, "cached_tokens") or 0
    cached_details: Mapping[str, Any] = in_details.get("cached_tokens_details") or {}
    cached_audio = _count(cached_details, "audio_tokens")
    cached_text = _count(cached_details, "text_tokens")
    if cached_audio is None and cached_text is None:
        # Text first: the cheaper kind, so the smaller discount.
        cached_text = min(cached, text_in)
        cached_audio = cached - cached_text
    elif cached_audio is None:
        cached_audio = max(cached - (cached_text or 0), 0)
    elif cached_text is None:
        cached_text = max(cached - cached_audio, 0)
    assert cached_text is not None
    # Never more cached tokens of a kind than the kind has: a fresh count does not go negative.
    cached_text, cached_audio = min(cached_text, text_in), min(cached_audio, audio_in)

    audio_out = _count(out_details, "audio_tokens")
    text_out = _count(out_details, "text_tokens")
    if audio_out is None and text_out is None:
        audio_out, text_out = (tokens_out, 0) if prices.audio_out >= prices.text_out else (0, tokens_out)
    elif audio_out is None:
        audio_out = max(tokens_out - (text_out or 0), 0)
    elif text_out is None:
        text_out = max(tokens_out - audio_out, 0)
    assert text_out is not None

    cost = (
        (text_in - cached_text) * prices.text_in
        + (audio_in - cached_audio) * prices.audio_in
        + (cached_text + cached_audio) * prices.cached_in
        + text_out * prices.text_out
        + audio_out * prices.audio_out
    ) / 1000
    return ResponseCost(round(cost, 8), text_in + audio_in, text_out + audio_out, cached_text + cached_audio)


def session_cap(
    reserved_usd: float, usd_per_minute: float | None, elapsed_s: float, minute_s: float
) -> float:
    """What the session may have spent `elapsed_s` after it opened: its reservation, and the rate
    for each minute begun when the catalogue sets one."""
    if usd_per_minute is None:
        return reserved_usd
    minutes = max(1, math.ceil(elapsed_s / minute_s))
    return min(reserved_usd, usd_per_minute * minutes)


def relayed_spend(payloads: list[Mapping[str, Any]], model: str) -> Spend | None:
    """The sum of the responses the relay priced, or `None` when it priced none (not a voice session,
    or one that never answered)."""
    priced = [p for p in payloads if p.get("priced_by") == "relay"]
    if not priced:
        return None
    return Spend(
        tokens_in=sum(int(p.get("tokens_in") or 0) for p in priced),
        tokens_out=sum(int(p.get("tokens_out") or 0) for p in priced),
        tokens_cached=sum(int(p.get("tokens_cached") or 0) for p in priced),
        cost_usd=round(sum(float(p.get("cost_usd") or 0.0) for p in priced), 6),
        requests=len(priced),
        models_used=[model],
    )


__all__ = [
    "RealtimePrices",
    "ResponseCost",
    "realtime_prices",
    "relayed_spend",
    "response_cost",
    "session_cap",
]
