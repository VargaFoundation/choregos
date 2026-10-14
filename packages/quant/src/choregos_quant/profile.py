# SPDX-License-Identifier: Apache-2.0
"""Anonymised universe profile: in-sample statistics only, no ticker, no price, no date."""

from __future__ import annotations

import hashlib
import math
from pathlib import Path
from typing import Any

from choregos_quant import metrics
from choregos_quant.bars import Series
from choregos_quant.campaign import Campaign
from choregos_quant.canon import dumps, r6, write_text

CLUSTER_THRESHOLD = 0.6


def anonymisation_order(symbols: list[str], seed: str) -> list[str]:
    """Deterministic, non-alphabetical order: sha256(seed + symbol)."""
    return sorted(symbols, key=lambda s: (hashlib.sha256(f"{seed}:{s}".encode()).hexdigest(), s))


def _correl(a: dict[str, float], b: dict[str, float]) -> float:
    common = sorted(set(a) & set(b))
    if len(common) < 3:
        return 0.0
    x, y = [a[d] for d in common], [b[d] for d in common]
    mx, my = metrics.mean(x), metrics.mean(y)
    sxx = sum((v - mx) ** 2 for v in x)
    syy = sum((v - my) ** 2 for v in y)
    if sxx <= 0 or syy <= 0:
        return 0.0
    return sum((u - mx) * (v - my) for u, v in zip(x, y, strict=True)) / math.sqrt(sxx * syy)


def _autocorr(x: list[float]) -> float:
    if len(x) < 3:
        return 0.0
    m = metrics.mean(x)
    den = sum((v - m) ** 2 for v in x)
    if den <= 0:
        return 0.0
    return sum((x[i] - m) * (x[i - 1] - m) for i in range(1, len(x))) / den


def build_profile(campaign: Campaign, bars: dict[str, Series]) -> tuple[dict[str, Any], dict[str, str]]:
    """Return (universe_profile, anonymisation mapping). Uses dates in [start, oos_start) only."""
    rets: dict[str, dict[str, float]] = {}
    for symbol in campaign.symbols:
        s = bars.get(symbol)
        if s is None:
            continue
        pairs = [
            (d, p) for d, p in zip(s.dates, s.prices, strict=True) if campaign.start <= d < campaign.oos_start
        ]
        if len(pairs) >= 3:
            rets[symbol] = {pairs[i][0]: pairs[i][1] / pairs[i - 1][1] - 1 for i in range(1, len(pairs))}
    order = anonymisation_order(list(rets), campaign.seed)
    names = {sym: f"P{i + 1:02d}" for i, sym in enumerate(order)}
    # Single-link clusters over the correlation of in-sample returns, numbered by first member.
    cluster: dict[str, int] = {}
    for sym in order:
        linked = {
            cluster[o] for o in order if o in cluster and _correl(rets[sym], rets[o]) >= CLUSTER_THRESHOLD
        }
        if linked:
            keep = min(linked)
            for o, c in list(cluster.items()):
                if c in linked:
                    cluster[o] = keep
            cluster[sym] = keep
        else:
            cluster[sym] = max(cluster.values(), default=0) + 1
    ranks = {
        c: i + 1
        for i, c in enumerate(
            sorted(set(cluster.values()), key=lambda c: min(order.index(s) for s in order if cluster[s] == c))
        )
    }
    products: dict[str, Any] = {}
    for sym in order:
        values = [rets[sym][d] for d in sorted(rets[sym])]
        products[names[sym]] = {
            "annualised_vol": r6(metrics.stdev(values) * math.sqrt(metrics.DAYS)),
            "annualised_return": r6(metrics.cagr(values)),
            "max_drawdown": r6(metrics.max_drawdown(values)),
            "autocorrelation_lag1": r6(_autocorr(values)),
            "correlation_cluster": f"C{ranks[cluster[sym]]}",
        }
    profile = {
        "products": products,
        "facts": {
            "products": len(order),
            "clusters": len(ranks),
            "in_sample_days": max((len(r) for r in rets.values()), default=0),
        },
    }
    return profile, {names[s]: s for s in order}


def write_profile(campaign: Campaign, bars: dict[str, Series], out: Path) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=True)
    profile, mapping = build_profile(campaign, bars)
    write_text(out / "universe_profile.json", dumps(profile))
    write_text(out / "anonymisation.json", dumps(mapping))
    write_text(out / "result.json", dumps({"facts": profile["facts"]}))
    return profile
