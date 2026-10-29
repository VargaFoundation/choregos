# SPDX-License-Identifier: Apache-2.0
"""Several values for one setting, used in turn (ADR 0089): API keys today, URLs and proxies later.

A connector's pooled secret field resolves to a list when it holds several keys. `Pool` picks one
per call and moves on when a vendor pushes back:

- `failover` (the default): the first usable value; the next one only while it cools down;
- `round_robin`: the next usable value at each call, to spread the load;
- a rate limit cools the value down for its `Retry-After` (60 s without one) and the call is made
  again at once with the next usable value; a refusal (401/403) benches it for 10 minutes;
- when every value cools down, the call fails with the adapter's own rate-limit error: never a
  silent call without a key.

A pool of one is the old behaviour exactly: one call, its error as it comes, no cool-down.

The cool-down state is per process (API, worker) and survives an adapter being rebuilt: it is keyed
by a digest of each value, never by the value. Nothing is shared between processes or persisted.
"""

from __future__ import annotations

import hashlib
import itertools
import time
from collections.abc import Awaitable, Callable, Sequence
from contextvars import ContextVar
from typing import Literal, TypeVar

R = TypeVar("R")
Strategy = Literal["failover", "round_robin"]
STRATEGIES: tuple[Strategy, ...] = ("failover", "round_robin")
DEFAULT_COOL_DOWN_S = 60.0
BENCH_S = 600.0

#: digest -> monotonic time until which the value rests. Process-wide, bounded by the keys in use.
_RESTING: dict[str, float] = {}
#: pool digest -> its round-robin counter.
_TURNS: dict[str, itertools.count[int]] = {}


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def as_values(setting: str | Sequence[str] | None) -> list[str]:
    """What a resolved configuration field holds, as a list: a string is a pool of one."""
    if setting is None or setting == "":
        return []
    if isinstance(setting, str):
        return [setting]
    return [str(v) for v in setting if v]


def strategy_of(setting: object) -> Strategy:
    """`config.key_rotation`, `failover` when absent; anything else is refused by the caller's check."""
    return "round_robin" if setting == "round_robin" else "failover"


def reset() -> None:
    """For tests: every value usable again."""
    _RESTING.clear()
    _TURNS.clear()


class Pool:
    def __init__(
        self,
        values: str | Sequence[str] | None,
        *,
        strategy: Strategy = "failover",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.values = as_values(values)
        self.strategy = strategy
        self._clock = clock
        self._digests = [_digest(v) for v in self.values]
        self._id = _digest("|".join(self._digests))
        self._current: ContextVar[str] = ContextVar(f"pool-{self._id[:12]}", default=self.first)

    def __len__(self) -> int:
        return len(self.values)

    def __repr__(self) -> str:
        return f"<Pool of {len(self)} ({self.strategy})>"

    @property
    def first(self) -> str:
        return self.values[0] if self.values else ""

    @property
    def current(self) -> str:
        """The value of the call in progress (each asyncio task sees its own)."""
        return self._current.get()

    def label(self, index: int) -> str:
        return f"key {index + 1} of {len(self)}"

    def rest(self, index: int, seconds: float) -> None:
        _RESTING[self._digests[index]] = self._clock() + max(seconds, 1.0)

    def _resting_until(self, index: int) -> float:
        return _RESTING.get(self._digests[index], 0.0)

    def order(self) -> list[int]:
        """The indexes to try, in order, for this call: usable ones only."""
        now = self._clock()
        indexes = list(range(len(self)))
        if self.strategy == "round_robin" and indexes:
            start = next(_TURNS.setdefault(self._id, itertools.count())) % len(indexes)
            indexes = indexes[start:] + indexes[:start]
        return [i for i in indexes if self._resting_until(i) <= now]

    def wait_s(self) -> int:
        """How long until the first value is usable again."""
        soonest = min((self._resting_until(i) for i in range(len(self))), default=0.0)
        return max(1, int(soonest - self._clock()) + 1)

    async def run(
        self,
        call: Callable[[], Awaitable[R]],
        *,
        rate_limited: type[Exception] | tuple[type[Exception], ...],
        refused: type[Exception] | tuple[type[Exception], ...],
        exhausted: Callable[[int], Exception],
        retry_after: Callable[[Exception], float | None] = lambda _e: None,
    ) -> R:
        """`call()` with `current` set to one value after another, until one answers."""
        if len(self) <= 1:
            return await call()
        limited_seen = False
        refusal: Exception | None = None
        for index in self.order():
            token = self._current.set(self.values[index])
            try:
                return await call()
            except rate_limited as limited:
                self.rest(index, retry_after(limited) or DEFAULT_COOL_DOWN_S)
                limited_seen = True
            except refused as refused_:
                self.rest(index, BENCH_S)
                refusal = _named(refused_, self.label(index))
            finally:
                self._current.reset(token)
        # Every key refused: say so, it needs a person. Any key only resting: it heals by itself.
        if refusal is not None and not limited_seen:
            raise refusal
        raise exhausted(self.wait_s())


def _named(error: Exception, label: str) -> Exception:
    """The same error, its message prefixed with the key's position — never the key."""
    try:
        return type(error)(f"{label}: {error}")
    except TypeError:
        return error
