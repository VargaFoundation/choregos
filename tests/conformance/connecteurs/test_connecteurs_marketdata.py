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


def serveur(cle_attendue: str) -> httpx.MockTransport:
    """A stand-in for the three providers: answers by host, and refuses a wrong key where one is used."""

    def handler(requete: httpx.Request) -> httpx.Response:
        cle = requete.url.params.get("apikey")
        if requete.url.host in {"stooq.com", "www.alphavantage.co"} and cle != cle_attendue:
            return httpx.Response(403, text="forbidden")
        if requete.url.host == "stooq.com":
            return httpx.Response(200, text=STOOQ)
        if requete.url.host == "www.alphavantage.co":
            return httpx.Response(200, json=ALPHA)
        return httpx.Response(200, json=YAHOO)

    return httpx.MockTransport(handler)


def ouvrir(nom: str, cle: str = SECRET) -> MarketDataProvider:
    sources = {"stooq": {"api_key": cle}, "alphavantage": {"api_key": cle}, "yahoo": {}, "fake": {}}
    if nom == "composite":
        fournisseur = build(
            "marketdata",
            "composite",
            {
                "sources": ["stooq", "alphavantage"],
                "stooq_api_key": cle,
                "alphavantage_api_key": cle,
            },
        )
        for _, source in fournisseur._sources:
            source._client = httpx.AsyncClient(transport=serveur(SECRET))
        return fournisseur  # type: ignore[no-any-return]
    fournisseur = build("marketdata", nom, sources[nom])
    if nom != "fake":
        fournisseur._client = httpx.AsyncClient(transport=serveur(SECRET))
    return fournisseur  # type: ignore[no-any-return]


TYPES = ["stooq", "yahoo", "alphavantage", "composite"]


def exemple(nom: str, schema: dict[str, Any]) -> Any:
    return {"symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-31"}[nom]


@pytest.mark.parametrize("nom", [*TYPES, "fake"])
async def test_each_operation_declares_a_closed_schema_the_implementation_follows(nom: str) -> None:
    import choregos_adapters.fakes.marketdata  # noqa: F401 - the `fake` type registers itself at import

    spec = spec_of("marketdata", nom if nom != "fake" else "stooq")
    assert spec is not None
    assert {o.name for o in spec.operations} == {"get_bars", "get_instrument"}
    for operation in spec.operations:
        assert operation.access == "read" and operation.default_policy == "allowed"
        schema = dict(operation.input_schema or {})
        Draft202012Validator.check_schema(schema)
        assert schema.get("additionalProperties") is False
        assert set(schema["required"]) <= set(schema["properties"])
        params = {k: exemple(k, v) for k, v in schema["properties"].items()}
        Draft202012Validator(schema).validate(params)
        # an argument outside the schema never reaches the provider
        assert not Draft202012Validator(schema).is_valid({**params, "url": "https://evil.test"})
        assert not Draft202012Validator(schema).is_valid({**params, "symbol": "AAPL/../x"})
        resultat = await ouvrir(nom).executer(operation.name, params)
        json.dumps(resultat)
        assert resultat["symbol"] == "AAPL" and resultat["provider"]
        if operation.name == "get_bars":
            dates = [b["date"] for b in resultat["bars"]]
            assert dates and dates == sorted(set(dates)), "sorted, one bar per date"
            assert set(resultat["bars"][0]) == {"date", "open", "high", "low", "close", "adj_close", "volume"}
            assert isinstance(resultat["bars"][0]["close"], float)
            assert isinstance(resultat["adjusted"], bool)
        else:
            assert resultat["type"] in {"stock", "etf", "unknown"}


@pytest.mark.parametrize("nom", ["stooq", "alphavantage", "composite"])
async def test_the_key_serves_and_appears_nowhere(nom: str, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    with structlog.testing.capture_logs() as evenements:
        cas = ouvrir(nom)
        resultats = [
            await cas.executer("get_bars", {"symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-31"}),
            await cas.executer("get_instrument", {"symbol": "AAPL"}),
            await cas.test(),
        ]
        intrus = ouvrir(nom, cle=MAUVAISE)
        with pytest.raises(MarketDataError) as refus:
            await intrus.executer("get_bars", {"symbol": "AAPL", "start": "2024-01-02", "end": "2024-01-31"})
    traces = [
        *(r.getMessage() for r in caplog.records),
        *(repr(e) for e in evenements),
        *(json.dumps(r, default=str) for r in resultats),
        str(refus.value),
        repr(cas),
        repr(intrus),
    ]
    assert [t for t in traces if SECRET in t or MAUVAISE in t] == []
