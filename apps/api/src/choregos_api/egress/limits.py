# SPDX-License-Identifier: Apache-2.0
"""Token buckets for the proxy's connection rates, with an injectable clock.

A rate is "new connections per minute": the bucket holds `per_minute` tokens (the burst) and gets
them back evenly. Several buckets are asked at once (a run's own, its run-wide one, the
organisation's for the host) and a connection is let through only if all of them have a token: none
is spent for a connection another bucket refuses.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Hashable, Iterable
from dataclasses import dataclass, field


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass
class RateLimiter:
    clock: Callable[[], float] = time.monotonic
    _buckets: dict[Hashable, _Bucket] = field(default_factory=dict)

    def _refill(self, key: Hashable, per_minute: int) -> _Bucket:
        now = self.clock()
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = self._buckets[key] = _Bucket(tokens=float(per_minute), updated=now)
        else:
            bucket.tokens = min(float(per_minute), bucket.tokens + (now - bucket.updated) * per_minute / 60)
            bucket.updated = now
        return bucket

    def acquire(self, wants: Iterable[tuple[Hashable, int | None]]) -> float:
        """0 when every bucket gave a token (and they are spent); otherwise the seconds to wait."""
        taken = [(self._refill(key, rate), rate) for key, rate in wants if rate is not None]
        wait = 0.0
        for bucket, rate in taken:
            if bucket.tokens < 1:
                wait = max(wait, (1 - bucket.tokens) * 60 / rate)
        if wait > 0:
            return wait
        for bucket, _ in taken:
            bucket.tokens -= 1
        return 0.0

    def forget_idle(self, older_than: float = 600) -> None:
        """Drop buckets nobody touched for a while (a finished run's)."""
        now = self.clock()
        for key in [k for k, b in self._buckets.items() if now - b.updated > older_than]:
            del self._buckets[key]


def retry_after(seconds: float) -> int:
    return max(1, math.ceil(seconds))
