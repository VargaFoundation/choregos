"""What every `marketdata` connector promises, whatever its source (S38-03).

The family is read-only, so the business-family suite (which wants a write to prove idempotence)
does not fit; the promises are the same in spirit, judged here for each type:

1. **Schemas followed**: each operation declares a closed input schema, the implementation takes
   what it allows, and what it returns has the family's shape (bars sorted, one per date, floats).
2. **Reads only**: no operation writes, the declared access says so, the policy defaults to allowed.
3. **No secret anywhere**: the key serves (the provider receives it, a wrong one is refused) but
   appears in no log, result, error or representation of the connector.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
import structlog
from choregos_adapters import build, spec_of
from choregos_adapters.marketdata import MarketDataError, MarketDataProvider
from jsonschema import Draft202012Validator

pytestmark = pytest.mark.conformance


@pytest.fixture(autouse=True)
def real_connectors(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CHOREGOS_FAKES=1`, set by the API's suite when everything runs together, makes `build()`
    answer the fake for every type: each promise would then be judged on the fake, and pass."""
    monkeypatch.setenv("CHOREGOS_FAKES", "0")


SECRET = "cle-marketdata-0b7f"
MAUVAISE = "cle-de-quelqu-un-d-autre"
STOOQ = "Date,Open,High,Low,Close,Volume\n2024-01-03,1,2,0.5,1.5,100\n2024-01-02,1,2,0.5,1.4,90\n"
ALPHA = {
    "Time Series (Daily)": {
        "2024-01-03": {"1. open": "1", "2. high": "2", "3. low": "0.5", "4. close": "1.5", "5. volume": "100"}
    },
    "bestMatches": [{"1. symbol": "AAPL", "2. name": "Apple", "3. type": "Equity", "8. currency": "USD"}],
}
YAHOO = {
    "chart": {
        "result": [
            {
                "meta": {"currency": "USD", "instrumentType": "EQUITY", "gmtoffset": 0},
                "timestamp": [1704205800],
                "indicators": {
                    "quote": [{"open": [1], "high": [2], "low": [0.5], "close": [1.5], "volume": [100]}]
                },
            }
        ],
        "error": None,
    }
}


TIINGO = [
    {"date": "2024-01-02T00:00:00.000Z", "open": 187.15, "high": 188.44, "low": 183.89, "close": 185.64,
     "volume": 82488674, "adjClose": 184.73, "splitFactor": 1.0, "divCash": 0.0},
    {"date": "2024-01-03T00:00:00.000Z", "open": 184.22, "high": 185.88, "low": 183.43, "close": 184.25,
     "volume": 58414460, "adjClose": 183.35, "splitFactor": 1.0, "divCash": 0.0},
]  # fmt: skip


def server(expected_key: str) -> httpx.MockTransport:
    """A stand-in for the four providers: answers by host, and refuses a wrong key where one is used."""

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.url.params.get("apikey")
        if request.url.host == "api.tiingo.com":
            # Tiingo's token is a header (S38-22): a wrong one is a 401, as Tiingo answers.
            if request.headers.get("Authorization") != f"Token {expected_key}":
                return httpx.Response(401, json={"detail": "Invalid token."})
            if request.url.path.endswith("/prices"):
                return httpx.Response(200, json=TIINGO)
            return httpx.Response(200, json={"ticker": "aapl", "name": "Apple Inc", "exchangeCode": "NASDAQ"})
        if request.url.host in {"stooq.com", "www.alphavantage.co"} and key != expected_key:
            return httpx.Response(403, text="forbidden")
        if request.url.host == "stooq.com":
            return httpx.Response(200, text=STOOQ)
        if request.url.host == "www.alphavantage.co":
            return httpx.Response(200, json=ALPHA)
        return httpx.Response(200, json=YAHOO)

    return httpx.MockTransport(handler)


def ouvrir(name: str, key: str = SECRET) -> MarketDataProvider:
    sources = {
        "stooq": {"api_key": key},
        "alphavantage": {"api_key": key},
        "tiingo": {"api_key": key},
        "yahoo": {},
        "fake": {},
    }
    if name == "composite":
        supplier = build(
            "marketdata",
            "composite",
            {
                "sources": ["stooq", "alphavantage", "tiingo"],
                "stooq_api_key": key,
                "alphavantage_api_key": key,
                "tiingo_api_key": key,
            },
        )
        for _, source in supplier._sources:
            # The provider's own headers (Tiingo's token) stay: only the network is replaced.
            source._client = httpx.AsyncClient(transport=server(SECRET), headers=source._client.headers)
        return supplier  # type: ignore[no-any-return]
    supplier = build("marketdata", name, sources[name])
    if name != "fake":
        supplier._client = httpx.AsyncClient(transport=server(SECRET), headers=supplier._client.headers)
    return supplier  # type: ignore[no-any-return]


TYPES = ["stooq", "yahoo", "alphavantage", "tiingo", "composite"]


def example(name: str, schema: dict[str, Any]) -> Any:
    return {"symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-31"}[name]


@pytest.mark.parametrize("name", [*TYPES, "fake"])
async def test_each_operation_declares_a_closed_schema_the_implementation_follows(name: str) -> None:
    import choregos_adapters.fakes.marketdata  # noqa: F401 - the `fake` type registers itself at import

    spec = spec_of("marketdata", name if name != "fake" else "stooq")
    assert spec is not None
    assert {o.name for o in spec.operations} == {"get_bars", "get_instrument"}
    for operation in spec.operations:
        assert operation.access == "read" and operation.default_policy == "allowed"
        schema = dict(operation.input_schema or {})
        Draft202012Validator.check_schema(schema)
        assert schema.get("additionalProperties") is False
        assert set(schema["required"]) <= set(schema["properties"])
        params = {k: example(k, v) for k, v in schema["properties"].items()}
        Draft202012Validator(schema).validate(params)
        # an argument outside the schema never reaches the provider
        assert not Draft202012Validator(schema).is_valid({**params, "url": "https://evil.test"})
        assert not Draft202012Validator(schema).is_valid({**params, "symbol": "AAPL/../x"})
        result = await ouvrir(name).executer(operation.name, params)
        json.dumps(result)
        assert result["symbol"] == "AAPL" and result["provider"]
        if operation.name == "get_bars":
            dates = [b["date"] for b in result["bars"]]
            assert dates and dates == sorted(set(dates)), "sorted, one bar per date"
            assert set(result["bars"][0]) == {"date", "open", "high", "low", "close", "adj_close", "volume"}
            assert isinstance(result["bars"][0]["close"], float)
            assert isinstance(result["adjusted"], bool)
        else:
            assert result["type"] in {"stock", "etf", "unknown"}


@pytest.mark.parametrize("name", ["stooq", "alphavantage", "tiingo", "composite"])
async def test_the_key_serves_and_appears_nowhere(name: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as events:
        cas = ouvrir(name)
        results = [
            await cas.executer("get_bars", {"symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-31"}),
            await cas.executer("get_instrument", {"symbol": "AAPL"}),
            await cas.test(),
        ]
        intrus = ouvrir(name, key=MAUVAISE)
        with pytest.raises(MarketDataError) as refusal:
            await intrus.executer("get_bars", {"symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-31"})
    traces = [
        *(r.getMessage() for r in caplog.records),
        *(repr(e) for e in events),
        *(json.dumps(r, default=str) for r in results),
        str(refusal.value),
        repr(cas),
        repr(intrus),
    ]
    assert [t for t in traces if SECRET in t or MAUVAISE in t] == []
