# SPDX-License-Identifier: Apache-2.0
"""What the `marketdata` connector types declare (ADR 0034): their operations, configuration and secrets.

Moved from the core's `tranibor_adapters.specs` and `families` (S83-08, ADR 0181 O4-4); the names, the
schemas and the operations are the ones S38-03 shipped, so an organisation connector declared before the
move validates unchanged.
"""

from __future__ import annotations

from typing import Any

from tranibor_adapters import ConnectorTypeSpec, OperationSpec
from tranibor_adapters.registry import build_input_schema

from .base import DATE_PATTERN, SYMBOL_PATTERN

#: The connector kind the family serves, and the name a job's `tools:` and a template's
#: `org_connectors:` give it.
KIND = "marketdata"

#: Daily bars and the description of a symbol, from free sources. Read-only: no write, so no approval;
#: the platform is the go-between (the sandbox's egress stays closed, keys stay out of the pod, every
#: call is counted).
OPERATIONS: tuple[OperationSpec, ...] = (
    OperationSpec(
        "get_bars",
        "read",
        "reads the daily bars (open, high, low, close, adjusted close, volume) of a stock or ETF "
        "between two dates, sorted, one per date",
        build_input_schema(
            "symbol",
            "start",
            "end",
            symbol={"type": "string", "pattern": SYMBOL_PATTERN, "description": "e.g. AAPL, MC.PA, ^GSPC"},
            start={"type": "string", "pattern": DATE_PATTERN, "description": "YYYY-MM-DD"},
            end={"type": "string", "pattern": DATE_PATTERN, "description": "YYYY-MM-DD, not before start"},
        ),
    ),
    OperationSpec(
        "get_instrument",
        "read",
        "reads what a symbol is: its name, whether it is a stock or an ETF, its currency and exchange",
        build_input_schema(
            "symbol", symbol={"type": "string", "pattern": SYMBOL_PATTERN, "description": "e.g. AAPL"}
        ),
    ),
)


def _object_schema(properties: dict[str, Any], required: tuple[str, ...] = ()) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = list(required)
    return schema


#: Several keys in one field (`api_key.2` …, ADR 0089): how they are used.
_KEY_ROTATION: dict[str, Any] = {
    "key_rotation": {
        "enum": ["failover", "round_robin"],
        "default": "failover",
        "description": "with several keys (`api_key.2` …): `failover` uses the first that answers, "
        "`round_robin` spreads the calls",
    }
}

#: Prices from free sources, read by the platform for a run whose sandbox has no egress. Keys stay
#: API-side (`secret_refs`).
SPECS: dict[str, ConnectorTypeSpec] = {
    "stooq": ConnectorTypeSpec(
        "Stooq (daily CSV, free)",
        (KIND,),
        _object_schema(
            {
                "suffix_map": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": 'ticker suffix -> Stooq market suffix, e.g. {"": ".us", ".L": ".uk"}',
                },
                "timeout_s": {"type": "number", "default": 20},
                **_KEY_ROTATION,
            }
        ),
        ("api_key",),
        operations=OPERATIONS,
        pooled_fields=("api_key",),
    ),
    "yahoo": ConnectorTypeSpec(
        "Yahoo Finance chart (unofficial: personal use only, may break)",
        (KIND,),
        _object_schema({"timeout_s": {"type": "number", "default": 20}}),
        operations=OPERATIONS,
    ),
    "alphavantage": ConnectorTypeSpec(
        "Alpha Vantage (free key: about 25 requests a day, unadjusted daily bars)",
        (KIND,),
        _object_schema(
            {
                "timeout_s": {"type": "number", "default": 20},
                "outputsize": {
                    "enum": ["auto", "compact"],
                    "default": "auto",
                    "description": "`compact`: always the last 100 trading days (free key); cross-check only",
                },
                **_KEY_ROTATION,
            }
        ),
        ("api_key",),
        operations=OPERATIONS,
        pooled_fields=("api_key",),
    ),
    "tiingo": ConnectorTypeSpec(
        "Tiingo (free key: 30+ years of daily history, internal and personal use only)",
        (KIND,),
        _object_schema({"timeout_s": {"type": "number", "default": 20}, **_KEY_ROTATION}),
        ("api_key",),
        operations=OPERATIONS,
        pooled_fields=("api_key",),
    ),
    "composite": ConnectorTypeSpec(
        "market data from several sources, with fallback and a cross-check of the closes",
        (KIND,),
        _object_schema(
            {
                "sources": {
                    "type": "array",
                    "items": {"enum": ["stooq", "yahoo", "alphavantage", "tiingo"]},
                    "minItems": 1,
                    "description": "provider types, in the order they are tried",
                },
                "source_configs": {
                    "type": "object",
                    "additionalProperties": {"type": "object"},
                    "description": 'sub-configuration by provider type, e.g. {"stooq": {"suffix_map": {}}}',
                },
                "cross_check": {"type": "boolean", "default": True},
                "tolerance": {"type": "number", "default": 0.02, "minimum": 0},
                **_KEY_ROTATION,
            },
            ("sources",),
        ),
        ("stooq_api_key", "alphavantage_api_key", "tiingo_api_key"),
        operations=OPERATIONS,
        pooled_fields=("stooq_api_key", "alphavantage_api_key", "tiingo_api_key"),
    ),
}
