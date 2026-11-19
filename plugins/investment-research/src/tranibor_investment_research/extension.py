# SPDX-License-Identifier: Apache-2.0
"""The extension of the `investment-research` plugin (ADR 0181 O4-4): what its trade needs inside the
platform's processes, registered through the core's seams when the API and the worker load the
`choregos.plugins` entry point.

- the connector kind `marketdata` in the contracts' open list (`CONNECTOR_KINDS`, ADR 0181 round C0);
- the `marketdata` connector types (`stooq`, `yahoo`, `alphavantage`, `tiingo`, `composite`), through
  `tranibor_adapters.register`, with the specs their organisation connectors are validated against;
- their fake (ADR 0005), registered as the kind's `fake` type: `CHOREGOS_FAKES=1` switches every
  `marketdata` connector to it, as it switches the core's;
- the eval scene `marketdata/default`, through `declare_scene`.

`register` is idempotent: the API loads the extensions twice (through the worker it imports, then in
`create_app`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from tranibor_adapters import register as register_connector
from tranibor_adapters.errors import ConfigurationError
from tranibor_adapters.fakes.scenes import declare_scene
from tranibor_adapters.http.pool import strategy_of
from tranibor_contracts import CONNECTOR_KINDS

from .marketdata import PROVIDERS, CompositeProvider, MarketDataProvider
from .marketdata import fake as _fake
from .marketdata.specs import KIND, SPECS

#: The entry point's name: the plugin's own (ADR 0181 point 4).
NAME = "investment-research"


def _provider(type_name: str, cfg: dict[str, Any], key: str | list[str] = "") -> MarketDataProvider:
    options: dict[str, Any] = {"timeout_s": float(cfg.get("timeout_s", 20))}
    if type_name == "stooq" and cfg.get("suffix_map"):
        options["suffix_map"] = dict(cfg["suffix_map"])
    if type_name == "alphavantage" and cfg.get("outputsize"):
        options["outputsize"] = str(cfg["outputsize"])
    if type_name != "yahoo":
        # A string, or a list when the field holds several keys (ADR 0089).
        options["api_key"] = key or cfg.get("api_key") or ""
        options["key_rotation"] = strategy_of(cfg.get("key_rotation"))
    return PROVIDERS[type_name](**options)


def _composite(cfg: dict[str, Any]) -> CompositeProvider:
    names = [str(n) for n in cfg.get("sources") or []]
    unknown = [n for n in names if n not in PROVIDERS]
    if not names or unknown:
        raise ConfigurationError(
            f"marketdata composite: `sources` must list provider types among {sorted(PROVIDERS)}"
            + (f" (unknown: {unknown})" if unknown else "")
        )
    configs = cfg.get("source_configs") or {}
    return CompositeProvider(
        [
            (
                n,
                _provider(
                    n,
                    {"key_rotation": cfg.get("key_rotation"), **dict(configs.get(n) or {})},
                    cfg.get(f"{n}_api_key") or "",
                ),
            )
            for n in names
        ],
        cross_check=bool(cfg.get("cross_check", True)),
        tolerance=float(cfg.get("tolerance", 0.02)),
    )


def _constructor(name: str) -> Callable[[dict[str, Any]], MarketDataProvider]:
    return lambda cfg: _provider(name, cfg)


def _fake_connector(cfg: dict[str, Any]) -> Any:
    """The process-wide fake of `CHOREGOS_FAKES=1`, scriptable by tests as `marketdata.fake.FAKE`."""
    return _fake.shared()


def _scene(name: str) -> tuple[Any, Any]:
    fake = _fake.FakeMarketData()
    return fake, fake


def register() -> None:
    """The `choregos.plugins` entry point of `tranibor-investment-research`."""
    CONNECTOR_KINDS.declare(KIND, plugin=NAME)
    for name in PROVIDERS:
        register_connector(KIND, name, SPECS[name])(_constructor(name))
    register_connector(KIND, "composite", SPECS["composite"])(_composite)
    register_connector(KIND, "fake")(_fake_connector)
    declare_scene(KIND, "composite", frozenset({"default"}), _scene)
