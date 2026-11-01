"""The `marketdata` family: each provider's parsing from real-shaped payloads (no network, an
`httpx.MockTransport`), the refusal of a symbol that would change the URL, the composite's fallback
order and cross-check, the fake's determinism."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
import pytest
from choregos_adapters.errors import AdapterError
from choregos_adapters.fakes.marketdata import FakeMarketData, seed_of, synthetic_bars
from choregos_adapters.marketdata import (
    AlphaVantageProvider,
    CompositeProvider,
    MarketDataError,
    RateLimited,
    StooqProvider,
    TiingoProvider,
    UnknownSymbol,
    YahooProvider,
)
from choregos_adapters.marketdata.composite import compare_closes

KEY = "k3y-t0p-s3cret"

STOOQ_CSV = """Date,Open,High,Low,Close,Volume
2024-01-03,184.22,185.88,183.43,184.25,58414460
2024-01-02,187.15,188.44,183.89,185.64,82488674
2024-01-02,187.15,188.44,183.89,185.64,82488674
2024-01-04,182.15,183.09,180.88,181.91,71983570
"""

# Yahoo: 2024-01-02 and 2024-01-03 at 14:30 UTC (New York open), one null row in between.
YAHOO_CHART = {
    "chart": {
        "result": [
            {
                "meta": {
                    "currency": "USD",
                    "symbol": "AAPL",
                    "instrumentType": "EQUITY",
                    "exchangeName": "NMS",
                    "fullExchangeName": "NasdaqGS",
                    "longName": "Apple Inc.",
                    "gmtoffset": -18000,
                },
                "timestamp": [1704209400, 1704295800, 1704382200],
                "indicators": {
                    "quote": [
                        {
                            "open": [187.15, 184.22, None],
                            "high": [188.44, 185.88, None],
                            "low": [183.89, 183.43, None],
                            "close": [185.64, 184.25, None],
                            "volume": [82488700, 58414500, None],
                        }
                    ],
                    "adjclose": [{"adjclose": [184.73, 183.35, None]}],
                },
            }
        ],
        "error": None,
    }
}

ALPHA_DAILY = {
    "Meta Data": {"2. Symbol": "AAPL"},
    "Time Series (Daily)": {
        "2024-01-04": {
            "1. open": "182.1500",
            "2. high": "183.0872",
            "3. low": "180.8800",
            "4. close": "181.9100",
            "5. volume": "71983570",
        },
        "2024-01-02": {
            "1. open": "187.1500",
            "2. high": "188.4400",
            "3. low": "183.8900",
            "4. close": "185.6400",
            "5. volume": "82488674",
        },
    },
}


def mock(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def fixed(
    status: int = 200, *, text: str | None = None, body: Any = None
) -> tuple[httpx.MockTransport, list]:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if body is not None:
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=text or "")

    return mock(handler), seen


async def no_sleep(_: float) -> None:
    return None


# ── stooq ────────────────────────────────────────────────────────────────────────────────


async def test_stooq_parse_sorted_deduplicated_floats_and_the_url() -> None:
    transport, seen = fixed(text=STOOQ_CSV)
    adapter = StooqProvider(transport=transport)
    response = await adapter.get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert [b["date"] for b in response["bars"]] == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert response["provider"] == "stooq" and response["adjusted"] is False
    first_bar = response["bars"][0]
    assert first_bar["close"] == 185.64 and first_bar["adj_close"] is None
    assert isinstance(first_bar["volume"], float)
    url = seen[0].url
    assert (url.host, url.path) == ("stooq.com", "/q/d/l/")
    assert dict(url.params) == {"s": "aapl.us", "d1": "20240102", "d2": "20240104", "i": "d"}


async def test_stooq_appends_the_api_key_only_when_configured_and_never_returns_it() -> None:
    transport, seen = fixed(text=STOOQ_CSV)
    await StooqProvider(transport=transport, api_key=KEY).get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert seen[0].url.params["apikey"] == KEY
    transport, seen = fixed(text=STOOQ_CSV)
    response = await StooqProvider(transport=transport).get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert "apikey" not in seen[0].url.params and KEY not in json.dumps(response)


@pytest.mark.parametrize(
    ("ticker", "wanted"),
    [
        ("AAPL", "aapl.us"),
        ("BRK-B", "brk-b.us"),
        ("VOD.L", "vod.uk"),
        ("SAP.DE", "sap.de"),
        ("MC.PA", "mc.fr"),
        ("aapl.us", "aapl.us"),
        ("^SPX", "^spx"),
        ("EURUSD=X", "eurusd"),
    ],
)
def test_stooq_suffix_map(ticker: str, wanted: str) -> None:
    assert StooqProvider().stooq_symbol(ticker) == wanted


def test_stooq_suffix_map_is_configurable_and_an_unmapped_suffix_is_refused() -> None:
    assert StooqProvider(suffix_map={"": ".uk"}).stooq_symbol("VOD") == "vod.uk"
    assert StooqProvider(suffix_map={".MI": ".it"}).stooq_symbol("ENI.MI") == "eni.it"
    with pytest.raises(MarketDataError, match="suffix_map"):
        StooqProvider().stooq_symbol("ENI.MI")


@pytest.mark.parametrize(
    ("raw", "error_type"),
    [
        ("No data", UnknownSymbol),
        ("Exceeded the daily hits limit", RateLimited),
        ("Get your apikey:\n\n1. Open https://stooq.com/q/d/?s=aapl.us&get_apikey", MarketDataError),
        # The browser check stooq answered every script on 2026-10-14: the source is down, the
        # symbol is not unknown.
        ('<!DOCTYPE html><html><head><meta charset="utf-8"></head><body><noscript>', MarketDataError),
        ("Symbol,Price\nAAPL,1", MarketDataError),
    ],
)
async def test_stooq_named_errors(raw: str, error_type: type[Exception]) -> None:
    transport, _ = fixed(text=raw)
    with pytest.raises(error_type) as capture:
        await StooqProvider(transport=transport).get_bars("ZZZZ", "2024-01-02", "2024-01-04")
    assert type(capture.value) is error_type


# ── yahoo ────────────────────────────────────────────────────────────────────────────────


async def test_yahoo_parse_local_dates_adjusted_close_and_skips_null_rows() -> None:
    transport, seen = fixed(body=YAHOO_CHART)
    response = await YahooProvider(transport=transport).get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert [b["date"] for b in response["bars"]] == ["2024-01-02", "2024-01-03"]
    assert response["currency"] == "USD" and response["adjusted"] is True
    assert response["bars"][0]["adj_close"] == 184.73 and response["bars"][0]["close"] == 185.64
    url = seen[0].url
    assert (url.host, url.path) == ("query1.finance.yahoo.com", "/v8/finance/chart/AAPL")
    assert url.params["interval"] == "1d" and url.params["includeAdjustedClose"] == "true"
    assert int(url.params["period1"]) == 1704153600 and int(url.params["period2"]) == 1704412800


async def test_yahoo_instrument_and_unknown_symbol_and_rate_limit() -> None:
    transport, _ = fixed(body=YAHOO_CHART)
    record = await YahooProvider(transport=transport).get_instrument("AAPL")
    assert record == {
        "symbol": "AAPL",
        "type": "stock",
        "provider": "yahoo",
        "name": "Apple Inc.",
        "currency": "USD",
        "exchange": "NasdaqGS",
    }
    inconnu = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data"}}}
    transport, _ = fixed(404, body=inconnu)
    with pytest.raises(UnknownSymbol):
        await YahooProvider(transport=transport).get_bars("ZZZZ", "2024-01-02", "2024-01-04")
    transport, _ = fixed(429, text="Too Many Requests")
    with pytest.raises(RateLimited):
        await YahooProvider(transport=transport).get_bars("AAPL", "2024-01-02", "2024-01-04")


async def test_yahoo_etf_type() -> None:
    payload = json.loads(json.dumps(YAHOO_CHART))
    payload["chart"]["result"][0]["meta"]["instrumentType"] = "ETF"
    transport, _ = fixed(body=payload)
    assert (await YahooProvider(transport=transport).get_instrument("SPY"))["type"] == "etf"


async def test_a_5xx_is_retried_a_bounded_number_of_times_then_named() -> None:
    made: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        made.append(1)
        return httpx.Response(503)

    adapter = YahooProvider(transport=mock(handler), sleep=no_sleep)
    with pytest.raises(MarketDataError, match="unavailable after 3 attempts"):
        await adapter.get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert len(made) == 3


async def test_a_429_is_not_retried() -> None:
    made: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        made.append(1)
        return httpx.Response(429)

    with pytest.raises(RateLimited):
        await YahooProvider(transport=mock(handler)).get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert len(made) == 1


# ── alphavantage ─────────────────────────────────────────────────────────────────────────


async def test_alphavantage_parse_and_url() -> None:
    transport, seen = fixed(body=ALPHA_DAILY)
    response = await AlphaVantageProvider(transport=transport, api_key=KEY).get_bars(
        "AAPL", "2024-01-02", "2024-01-31"
    )
    assert [b["date"] for b in response["bars"]] == ["2024-01-02", "2024-01-04"]
    assert response["bars"][1]["close"] == 181.91 and response["bars"][0]["adj_close"] is None
    assert response["adjusted"] is False
    url = seen[0].url
    assert (url.host, url.path) == ("www.alphavantage.co", "/query")
    assert url.params["function"] == "TIME_SERIES_DAILY" and url.params["apikey"] == KEY
    assert url.params["outputsize"] == "full", "a start far in the past asks for the full series"
    assert KEY not in json.dumps(response)


async def test_alphavantage_compact_asks_for_the_recent_days_whatever_the_range() -> None:
    """A free key refuses `full` (a premium feature): in `compact`, a multi-year range still gets the
    last 100 trading days, and the provider says it lacks the full history (S38-21)."""
    transport, seen = fixed(body=ALPHA_DAILY)
    compact = AlphaVantageProvider(transport=transport, api_key=KEY, outputsize="compact")
    response = await compact.get_bars("AAPL", "2019-01-02", "2024-01-31")
    assert seen[0].url.params["outputsize"] == "compact"
    assert [b["date"] for b in response["bars"]] == ["2024-01-02", "2024-01-04"]
    assert compact.full_history is False
    assert AlphaVantageProvider(api_key=KEY).full_history is True
    with pytest.raises(MarketDataError):
        AlphaVantageProvider(api_key=KEY, outputsize="full")


# ── tiingo ───────────────────────────────────────────────────────────────────────────────

# AAPL around its 4-for-1 split of 2020-08-31: raw closes before it, split factor 4 on the day.
TIINGO_ROWS = [
    {"date": "2020-08-28T00:00:00.000Z", "open": 504.05, "high": 505.77, "low": 498.31, "close": 499.23,
     "volume": 46907479, "adjClose": 121.37, "splitFactor": 1.0, "divCash": 0.0},
    {"date": "2020-08-31T00:00:00.000Z", "open": 127.58, "high": 131.0, "low": 126.0, "close": 129.04,
     "volume": 225702700, "adjClose": 125.47, "splitFactor": 4.0, "divCash": 0.0},
    {"date": "2020-09-01T00:00:00.000Z", "open": 132.76, "high": 134.8, "low": 130.53, "close": 134.18,
     "volume": 151948100, "adjClose": 130.47, "splitFactor": 1.0, "divCash": 0.0},
]  # fmt: skip


async def test_tiingo_closes_are_split_adjusted_like_the_other_sources() -> None:
    """A composite compares `close`, and Yahoo's is split-adjusted: Tiingo's raw close before a split
    would read as a 300 % divergence. The request runs to today, so a later split rescales it too;
    the key travels in the header, never in the URL (S38-22)."""
    transport, seen = fixed(body=TIINGO_ROWS)
    response = await TiingoProvider(transport=transport, api_key=KEY).get_bars(
        "AAPL", "2020-08-28", "2020-08-31"
    )
    before, day = response["bars"]
    assert [b["date"] for b in response["bars"]] == ["2020-08-28", "2020-08-31"], "inside the range only"
    assert before["close"] == pytest.approx(499.23 / 4) and before["volume"] == pytest.approx(46907479 * 4)
    assert day["close"] == 129.04, "the split's own day is already post-split"
    assert before["adj_close"] == 121.37 and response["adjusted"] is True
    url = seen[0].url
    assert (url.host, url.path) == ("api.tiingo.com", "/tiingo/daily/aapl/prices")
    assert url.params["startDate"] == "2020-08-28" and url.params["endDate"] >= date.today().isoformat()
    assert seen[0].headers["Authorization"] == f"Token {KEY}"
    assert KEY not in str(url) and KEY not in json.dumps(response)


@pytest.mark.parametrize(
    ("status", "body", "error"),
    [
        (404, {}, UnknownSymbol),
        (200, {"detail": "Error: Ticker 'NOPE' not found"}, UnknownSymbol),
        (401, {"detail": f"Invalid token {KEY}"}, MarketDataError),
        (200, {"detail": f"You have run over your hourly request allocation, key {KEY}"}, RateLimited),
        (200, {"unexpected": True}, MarketDataError),
    ],
)
async def test_tiingo_named_errors_and_the_key_is_scrubbed(
    status: int, body: dict, error: type[Exception]
) -> None:
    transport, _ = fixed(status, body=body)
    with pytest.raises(error) as capture:
        await TiingoProvider(transport=transport, api_key=KEY).get_bars("NOPE", "2024-01-02", "2024-01-04")
    assert KEY not in str(capture.value)
    if status == 401:
        assert not isinstance(capture.value, UnknownSymbol), "a refused key is not an unknown symbol"


async def test_tiingo_instrument_and_its_key() -> None:
    transport, _ = fixed(
        body={"ticker": "spy", "name": "SPDR S&P 500 ETF Trust", "exchangeCode": "NYSE ARCA"}
    )
    record = await TiingoProvider(transport=transport, api_key=KEY).get_instrument("SPY")
    assert record == {
        "symbol": "SPY",
        "type": "unknown",
        "provider": "tiingo",
        "name": "SPDR S&P 500 ETF Trust",
        "exchange": "NYSE ARCA",
    }
    with pytest.raises(MarketDataError, match="api_key"):
        TiingoProvider()


def test_alphavantage_requires_its_key() -> None:
    with pytest.raises(MarketDataError, match="api_key"):
        AlphaVantageProvider()


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        ({"Error Message": "Invalid API call."}, UnknownSymbol),
        ({"Note": "Thank you for using Alpha Vantage! 25 requests per day."}, RateLimited),
        ({"Information": f"premium endpoint, key {KEY}"}, RateLimited),
    ],
)
async def test_alphavantage_named_errors_and_the_key_is_scrubbed(
    payload: dict, error_type: type[Exception]
) -> None:
    transport, _ = fixed(body=payload)
    with pytest.raises(error_type) as capture:
        await AlphaVantageProvider(transport=transport, api_key=KEY).get_bars(
            "AAPL", "2024-01-02", "2024-01-04"
        )
    assert KEY not in str(capture.value)


async def test_alphavantage_instrument() -> None:
    payload = {
        "bestMatches": [
            {"1. symbol": "SPYG", "2. name": "SPDR Growth", "3. type": "ETF", "8. currency": "USD"},
            {"1. symbol": "SPY", "2. name": "SPDR S&P 500", "3. type": "ETF", "8. currency": "USD"},
        ]
    }
    transport, _ = fixed(body=payload)
    record = await AlphaVantageProvider(transport=transport, api_key=KEY).get_instrument("SPY")
    assert record["name"] == "SPDR S&P 500" and record["type"] == "etf" and record["currency"] == "USD"


async def test_a_network_failure_never_carries_the_url_or_the_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {request.url}")

    adapter = AlphaVantageProvider(transport=mock(handler), api_key=KEY, sleep=no_sleep)
    with pytest.raises(MarketDataError) as capture:
        await adapter.get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert KEY not in str(capture.value) and "alphavantage.co" not in str(capture.value)
    assert KEY not in repr(adapter)


# ── validation ───────────────────────────────────────────────────────────────────────────

HOSTILE_SYMBOLS = [
    "AAPL/../../admin",
    "AAPL?x=1",
    "AAPL#frag",
    "AAPL@evil.test",
    "evil.test/AAPL",
    "AAPL\r\nHost: evil.test",
    "AAPL%2F..",
    "A" * 21,
    "",
    "..",
    ".hidden",
    "AA..PL",
    "AAPL ",
    "AAPL:8080",
]


@pytest.mark.parametrize("ticker", HOSTILE_SYMBOLS)
@pytest.mark.parametrize(
    "factory",
    [
        StooqProvider,
        YahooProvider,
        lambda **k: AlphaVantageProvider(api_key=KEY, **k),
        lambda **k: TiingoProvider(api_key=KEY, **k),
    ],
)
async def test_a_symbol_cannot_change_the_url(ticker: str, factory: Any) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="")

    adapter = factory(transport=mock(handler))
    for pending_call in (
        adapter.get_bars(ticker, "2024-01-02", "2024-01-04"),
        adapter.get_instrument(ticker),
    ):
        with pytest.raises(MarketDataError):
            await pending_call
    assert seen == [], "nothing left the process"


async def test_a_valid_symbol_with_special_characters_stays_one_path_segment() -> None:
    transport, seen = fixed(body=YAHOO_CHART)
    await YahooProvider(transport=transport).get_bars("^GSPC", "2024-01-02", "2024-01-04")
    await YahooProvider(transport=transport).get_bars("EURUSD=X", "2024-01-02", "2024-01-04")
    assert [v.url.host for v in seen] == ["query1.finance.yahoo.com"] * 2
    assert seen[0].url.raw_path.startswith(b"/v8/finance/chart/%5EGSPC?")
    assert seen[1].url.raw_path.startswith(b"/v8/finance/chart/EURUSD%3DX?")


@pytest.mark.parametrize(
    ("start_date", "end_date"),
    [("2024-1-2", "2024-01-04"), ("20240102", "2024-01-04"), ("2024-02-30", "2024-03-01"),
     ("2024-01-05", "2024-01-04"), ("2024-01-02", "demain"), ("", "")],
)  # fmt: skip
async def test_dates_are_iso_and_ordered(start_date: str, end_date: str) -> None:
    adapter = FakeMarketData()
    with pytest.raises(MarketDataError):
        await adapter.get_bars("AAPL", start_date, end_date)


async def test_execute_refuses_an_unexpected_argument_and_an_unknown_operation() -> None:
    adapter = FakeMarketData()
    with pytest.raises(MarketDataError, match="unexpected"):
        await adapter.execute("get_bars", {"symbol": "A", "start": "2024-01-02", "end": "2024-01-03", "x": 1})
    with pytest.raises(AdapterError, match="no operation"):
        await adapter.execute("delete_everything", {})


# ── composite ────────────────────────────────────────────────────────────────────────────


def provider_with(bars: list[dict[str, Any]] | Exception, name: str) -> Any:
    class Scripted(FakeMarketData):
        async def get_bars(self, symbol: str, start: str, end: str) -> dict[str, Any]:
            self.calls.append(("get_bars", {}))
            if isinstance(bars, Exception):
                raise bars
            return {"symbol": symbol, "provider": name, "adjusted": False, "bars": bars}

    return Scripted()


def bar(day: str, close: float) -> dict[str, Any]:
    return {
        "date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "adj_close": None,
        "volume": 1.0,
    }


async def test_composite_falls_back_in_order_on_error_and_on_empty() -> None:
    a = provider_with(RateLimited("a: quota"), "a")
    b = provider_with([], "b")
    c = provider_with([bar("2024-01-02", 10.0)], "c")
    d = provider_with([bar("2024-01-02", 10.0)], "d")
    composite = CompositeProvider([("a", a), ("b", b), ("c", c), ("d", d)], cross_check=False)
    response = await composite.get_bars("X", "2024-01-02", "2024-01-03")
    assert response["provider"] == "c" and response["cross_check"] is None
    assert [e["source"] for e in response["fallbacks"]] == ["a", "b"]
    assert d.calls == [], "without cross_check the later sources are not asked"


async def test_composite_cross_check_math() -> None:
    primary_rows = provider_with(
        [bar("2024-01-02", 100.0), bar("2024-01-03", 100.0), bar("2024-01-04", 100.0)], "a"
    )
    second = provider_with([bar("2024-01-03", 101.0), bar("2024-01-04", 110.0), bar("2024-01-05", 1.0)], "b")
    response = await CompositeProvider([("a", primary_rows), ("b", second)], tolerance=0.02).get_bars(
        "X", "2024-01-02", "2024-01-05"
    )
    assert response["provider"] == "a" and response["bars"][0]["date"] == "2024-01-02"
    check = response["cross_check"]
    assert check["source"] == "b" and check["common_days"] == 2
    assert check["max_rel_diff"] == pytest.approx(0.10)
    assert check["diverging_days"] == 1 and check["diverging_dates"] == ["2024-01-04"]


def test_compare_closes_edges() -> None:
    assert compare_closes([], [], 0.02) == {
        "common_days": 0, "max_rel_diff": 0.0, "diverging_days": 0, "diverging_dates": [],
    }  # fmt: skip
    egal = compare_closes([bar("2024-01-02", 5.0)], [bar("2024-01-02", 5.0)], 0.0)
    assert egal["diverging_days"] == 0, "tolerance is strict: equal never diverges"


async def test_composite_cross_check_uses_the_next_source_that_answers() -> None:
    a = provider_with([bar("2024-01-02", 100.0)], "a")
    b = provider_with(UnknownSymbol("b: no"), "b")
    c = provider_with([bar("2024-01-02", 100.5)], "c")
    response = await CompositeProvider([("a", a), ("b", b), ("c", c)]).get_bars(
        "X", "2024-01-02", "2024-01-03"
    )
    assert response["cross_check"]["source"] == "c" and response["cross_check"]["diverging_days"] == 0
    assert [e["source"] for e in response["fallbacks"]] == ["b"]


async def test_composite_never_takes_a_recent_days_source_as_the_primary() -> None:
    """Listed first, a source with the recent days only does not give the bars: the next full source
    does, and the partial one cross-checks it on the days both have (S38-21)."""
    long_run = [bar("2019-01-02", 50.0), bar("2024-01-02", 100.0), bar("2024-01-03", 101.0)]
    recent = provider_with([bar("2024-01-02", 100.0), bar("2024-01-03", 101.0)], "av")
    recent.full_history = False
    response = await CompositeProvider(
        [("av", recent), ("yahoo", provider_with(long_run, "yahoo"))]
    ).get_bars("X", "2019-01-02", "2024-01-03")
    assert response["provider"] == "yahoo" and response["bars"] == long_run
    assert response["cross_check"]["source"] == "av" and response["cross_check"]["common_days"] == 2

    after = await CompositeProvider([("yahoo", provider_with(long_run, "yahoo")), ("av", recent)]).get_bars(
        "X", "2019-01-02", "2024-01-03"
    )
    assert after["provider"] == "yahoo" and after["cross_check"]["common_days"] == 2

    alone = CompositeProvider([("av", recent), ("yahoo", provider_with(RateLimited("y"), "yahoo"))])
    with pytest.raises(MarketDataError) as capture:
        await alone.get_bars("X", "2019-01-02", "2024-01-03")
    assert not isinstance(capture.value, UnknownSymbol), "the symbol exists, its history is missing"


async def test_composite_all_failed_all_empty_and_unknown() -> None:
    inconnus = CompositeProvider(
        [("a", provider_with(UnknownSymbol("a"), "a")), ("b", provider_with(UnknownSymbol("b"), "b"))]
    )
    with pytest.raises(UnknownSymbol):
        await inconnus.get_bars("X", "2024-01-02", "2024-01-03")
    mixte = CompositeProvider(
        [("a", provider_with(UnknownSymbol("a"), "a")), ("b", provider_with(RateLimited("b"), "b"))]
    )
    with pytest.raises(MarketDataError) as capture:
        await mixte.get_bars("X", "2024-01-02", "2024-01-03")
    assert not isinstance(capture.value, UnknownSymbol)
    vides = CompositeProvider([("a", provider_with([], "a")), ("b", provider_with([], "b"))])
    assert (await vides.get_bars("X", "2024-01-02", "2024-01-03"))["bars"] == []


async def test_composite_instrument_prefers_a_source_that_knows_the_type() -> None:
    transport, _ = fixed(body=YAHOO_CHART)
    stooq = StooqProvider()
    composite = CompositeProvider([("stooq", stooq), ("yahoo", YahooProvider(transport=transport))])
    assert (await composite.get_instrument("AAPL"))["type"] == "stock"
    single = CompositeProvider([("stooq", stooq)])
    assert (await single.get_instrument("AAPL"))["type"] == "unknown"


def test_registry_builds_the_family_and_the_composite_gets_its_secrets_by_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_adapters import build, spec_of

    # `CHOREGOS_FAKES=1` (set by the API's suite when everything runs together) would build the fake.
    monkeypatch.setenv("CHOREGOS_FAKES", "0")

    for name in ("stooq", "yahoo", "alphavantage", "tiingo", "composite"):
        spec = spec_of("marketdata", name)
        assert spec is not None and {o.name for o in spec.operations} == {"get_bars", "get_instrument"}
        assert {o.access for o in spec.operations} == {"read"}
    assert spec_of("marketdata", "stooq").secret_fields == ("api_key",)  # type: ignore[union-attr]
    composite = build(
        "marketdata",
        "composite",
        {"sources": ["stooq", "alphavantage"], "alphavantage_api_key": KEY, "stooq_api_key": KEY},
    )
    assert [name for name, _ in composite._sources] == ["stooq", "alphavantage"]
    compact = build(
        "marketdata",
        "composite",
        {
            "sources": ["yahoo", "alphavantage"],
            "source_configs": {"alphavantage": {"outputsize": "compact"}},
            "alphavantage_api_key": KEY,
        },
    )
    assert [s.full_history for _, s in compact._sources] == [True, False], "source_configs reach the provider"
    three = build(
        "marketdata",
        "composite",
        {"sources": ["yahoo", "tiingo", "alphavantage"], "tiingo_api_key": KEY, "alphavantage_api_key": KEY},
    )
    assert [name for name, _ in three._sources] == ["yahoo", "tiingo", "alphavantage"]
    assert spec_of("marketdata", "tiingo").secret_fields == ("api_key",)  # type: ignore[union-attr]
    assert KEY not in repr(composite)
    from choregos_adapters.errors import ConfigurationError

    for mauvaise in ({}, {"sources": ["bloomberg"]}, {"sources": ["alphavantage"]}):
        with pytest.raises((ConfigurationError, MarketDataError)):
            build("marketdata", "composite", mauvaise)


# ── fake ─────────────────────────────────────────────────────────────────────────────────


async def test_fake_is_deterministic_business_days_only_and_window_independent() -> None:
    a = await FakeMarketData().get_bars("AAPL", "2024-01-01", "2024-03-31")
    b = await FakeMarketData().get_bars("AAPL", "2024-01-01", "2024-03-31")
    assert a == b
    dates = [x["date"] for x in a["bars"]]
    assert dates == sorted(set(dates)) and len(dates) == 65
    assert all(date.fromisoformat(d).weekday() < 5 for d in dates)
    fenetre = await FakeMarketData().get_bars("AAPL", "2024-02-01", "2024-02-29")
    assert fenetre["bars"] == [x for x in a["bars"] if "2024-02-01" <= x["date"] <= "2024-02-29"]
    another = await FakeMarketData().get_bars("MSFT", "2024-01-01", "2024-03-31")
    assert another["bars"] != a["bars"]
    for x in a["bars"]:
        assert x["low"] <= min(x["open"], x["close"]) <= max(x["open"], x["close"]) <= x["high"]
        assert isinstance(x["close"], float) and x["close"] > 0
    assert seed_of("aapl") == seed_of("AAPL")
    assert synthetic_bars("AAPL", date(2024, 1, 6), date(2024, 1, 7)) == [], "a weekend has no bar"


async def test_fake_scripting_fixtures_instruments_errors_and_calls() -> None:
    scripted = FakeMarketData(
        fixtures={"FIX": [bar("2024-01-03", 2.0), bar("2024-01-02", 1.0), bar("2023-01-02", 9.0)]},
        instruments={"FIX": {"type": "etf", "name": "Fixture"}},
        errors={"BOOM": RateLimited("fake: slow down")},
    )
    response = await scripted.get_bars("FIX", "2024-01-01", "2024-01-31")
    assert [b["close"] for b in response["bars"]] == [1.0, 2.0]
    assert (await scripted.get_instrument("FIX"))["type"] == "etf"
    with pytest.raises(RateLimited):
        await scripted.get_bars("BOOM", "2024-01-01", "2024-01-31")
    with pytest.raises(UnknownSymbol):
        await scripted.get_instrument("NOPE1")
    assert [c[0] for c in scripted.calls] == ["get_bars", "get_instrument", "get_bars", "get_instrument"]
