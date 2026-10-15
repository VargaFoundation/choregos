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
    vues: list[httpx.Request] = []

    def handler(requete: httpx.Request) -> httpx.Response:
        vues.append(requete)
        if body is not None:
            return httpx.Response(status, json=body)
        return httpx.Response(status, text=text or "")

    return mock(handler), vues


async def no_sleep(_: float) -> None:
    return None


# ── stooq ────────────────────────────────────────────────────────────────────────────────


async def test_stooq_parse_sorted_deduplicated_floats_and_the_url() -> None:
    transport, vues = fixed(text=STOOQ_CSV)
    fournisseur = StooqProvider(transport=transport)
    reponse = await fournisseur.get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert [b["date"] for b in reponse["bars"]] == ["2024-01-02", "2024-01-03", "2024-01-04"]
    assert reponse["provider"] == "stooq" and reponse["adjusted"] is False
    premiere = reponse["bars"][0]
    assert premiere["close"] == 185.64 and premiere["adj_close"] is None
    assert isinstance(premiere["volume"], float)
    url = vues[0].url
    assert (url.host, url.path) == ("stooq.com", "/q/d/l/")
    assert dict(url.params) == {"s": "aapl.us", "d1": "20240102", "d2": "20240104", "i": "d"}


async def test_stooq_appends_the_api_key_only_when_configured_and_never_returns_it() -> None:
    transport, vues = fixed(text=STOOQ_CSV)
    await StooqProvider(transport=transport, api_key=KEY).get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert vues[0].url.params["apikey"] == KEY
    transport, vues = fixed(text=STOOQ_CSV)
    reponse = await StooqProvider(transport=transport).get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert "apikey" not in vues[0].url.params and KEY not in json.dumps(reponse)


@pytest.mark.parametrize(
    ("symbole", "attendu"),
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
def test_stooq_suffix_map(symbole: str, attendu: str) -> None:
    assert StooqProvider().stooq_symbol(symbole) == attendu


def test_stooq_suffix_map_is_configurable_and_an_unmapped_suffix_is_refused() -> None:
    assert StooqProvider(suffix_map={"": ".uk"}).stooq_symbol("VOD") == "vod.uk"
    assert StooqProvider(suffix_map={".MI": ".it"}).stooq_symbol("ENI.MI") == "eni.it"
    with pytest.raises(MarketDataError, match="suffix_map"):
        StooqProvider().stooq_symbol("ENI.MI")


@pytest.mark.parametrize(
    ("texte", "erreur"),
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
async def test_stooq_named_errors(texte: str, erreur: type[Exception]) -> None:
    transport, _ = fixed(text=texte)
    with pytest.raises(erreur) as capture:
        await StooqProvider(transport=transport).get_bars("ZZZZ", "2024-01-02", "2024-01-04")
    assert type(capture.value) is erreur


# ── yahoo ────────────────────────────────────────────────────────────────────────────────


async def test_yahoo_parse_local_dates_adjusted_close_and_skips_null_rows() -> None:
    transport, vues = fixed(body=YAHOO_CHART)
    reponse = await YahooProvider(transport=transport).get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert [b["date"] for b in reponse["bars"]] == ["2024-01-02", "2024-01-03"]
    assert reponse["currency"] == "USD" and reponse["adjusted"] is True
    assert reponse["bars"][0]["adj_close"] == 184.73 and reponse["bars"][0]["close"] == 185.64
    url = vues[0].url
    assert (url.host, url.path) == ("query1.finance.yahoo.com", "/v8/finance/chart/AAPL")
    assert url.params["interval"] == "1d" and url.params["includeAdjustedClose"] == "true"
    assert int(url.params["period1"]) == 1704153600 and int(url.params["period2"]) == 1704412800


async def test_yahoo_instrument_and_unknown_symbol_and_rate_limit() -> None:
    transport, _ = fixed(body=YAHOO_CHART)
    fiche = await YahooProvider(transport=transport).get_instrument("AAPL")
    assert fiche == {
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
    corps = json.loads(json.dumps(YAHOO_CHART))
    corps["chart"]["result"][0]["meta"]["instrumentType"] = "ETF"
    transport, _ = fixed(body=corps)
    assert (await YahooProvider(transport=transport).get_instrument("SPY"))["type"] == "etf"


async def test_a_5xx_is_retried_a_bounded_number_of_times_then_named() -> None:
    appels: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        appels.append(1)
        return httpx.Response(503)

    fournisseur = YahooProvider(transport=mock(handler), sleep=no_sleep)
    with pytest.raises(MarketDataError, match="unavailable after 3 attempts"):
        await fournisseur.get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert len(appels) == 3


async def test_a_429_is_not_retried() -> None:
    appels: list[int] = []

    def handler(_: httpx.Request) -> httpx.Response:
        appels.append(1)
        return httpx.Response(429)

    with pytest.raises(RateLimited):
        await YahooProvider(transport=mock(handler)).get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert len(appels) == 1


# ── alphavantage ─────────────────────────────────────────────────────────────────────────


async def test_alphavantage_parse_and_url() -> None:
    transport, vues = fixed(body=ALPHA_DAILY)
    reponse = await AlphaVantageProvider(transport=transport, api_key=KEY).get_bars(
        "AAPL", "2024-01-02", "2024-01-31"
    )
    assert [b["date"] for b in reponse["bars"]] == ["2024-01-02", "2024-01-04"]
    assert reponse["bars"][1]["close"] == 181.91 and reponse["bars"][0]["adj_close"] is None
    assert reponse["adjusted"] is False
    url = vues[0].url
    assert (url.host, url.path) == ("www.alphavantage.co", "/query")
    assert url.params["function"] == "TIME_SERIES_DAILY" and url.params["apikey"] == KEY
    assert url.params["outputsize"] == "full", "a start far in the past asks for the full series"
    assert KEY not in json.dumps(reponse)


def test_alphavantage_requires_its_key() -> None:
    with pytest.raises(MarketDataError, match="api_key"):
        AlphaVantageProvider()


@pytest.mark.parametrize(
    ("corps", "erreur"),
    [
        ({"Error Message": "Invalid API call."}, UnknownSymbol),
        ({"Note": "Thank you for using Alpha Vantage! 25 requests per day."}, RateLimited),
        ({"Information": f"premium endpoint, key {KEY}"}, RateLimited),
    ],
)
async def test_alphavantage_named_errors_and_the_key_is_scrubbed(
    corps: dict, erreur: type[Exception]
) -> None:
    transport, _ = fixed(body=corps)
    with pytest.raises(erreur) as capture:
        await AlphaVantageProvider(transport=transport, api_key=KEY).get_bars(
            "AAPL", "2024-01-02", "2024-01-04"
        )
    assert KEY not in str(capture.value)


async def test_alphavantage_instrument() -> None:
    corps = {
        "bestMatches": [
            {"1. symbol": "SPYG", "2. name": "SPDR Growth", "3. type": "ETF", "8. currency": "USD"},
            {"1. symbol": "SPY", "2. name": "SPDR S&P 500", "3. type": "ETF", "8. currency": "USD"},
        ]
    }
    transport, _ = fixed(body=corps)
    fiche = await AlphaVantageProvider(transport=transport, api_key=KEY).get_instrument("SPY")
    assert fiche["name"] == "SPDR S&P 500" and fiche["type"] == "etf" and fiche["currency"] == "USD"


async def test_a_network_failure_never_carries_the_url_or_the_key() -> None:
    def handler(requete: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"boom {requete.url}")

    fournisseur = AlphaVantageProvider(transport=mock(handler), api_key=KEY, sleep=no_sleep)
    with pytest.raises(MarketDataError) as capture:
        await fournisseur.get_bars("AAPL", "2024-01-02", "2024-01-04")
    assert KEY not in str(capture.value) and "alphavantage.co" not in str(capture.value)
    assert KEY not in repr(fournisseur)


# ── validation ───────────────────────────────────────────────────────────────────────────

HOSTILES = [
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


@pytest.mark.parametrize("symbole", HOSTILES)
@pytest.mark.parametrize(
    "fabrique", [StooqProvider, YahooProvider, lambda **k: AlphaVantageProvider(api_key=KEY, **k)]
)
async def test_a_symbol_cannot_change_the_url(symbole: str, fabrique: Any) -> None:
    vues: list[httpx.Request] = []

    def handler(requete: httpx.Request) -> httpx.Response:
        vues.append(requete)
        return httpx.Response(200, text="")

    fournisseur = fabrique(transport=mock(handler))
    for appel in (
        fournisseur.get_bars(symbole, "2024-01-02", "2024-01-04"),
        fournisseur.get_instrument(symbole),
    ):
        with pytest.raises(MarketDataError):
            await appel
    assert vues == [], "nothing left the process"


async def test_a_valid_symbol_with_special_characters_stays_one_path_segment() -> None:
    transport, vues = fixed(body=YAHOO_CHART)
    await YahooProvider(transport=transport).get_bars("^GSPC", "2024-01-02", "2024-01-04")
    await YahooProvider(transport=transport).get_bars("EURUSD=X", "2024-01-02", "2024-01-04")
    assert [v.url.host for v in vues] == ["query1.finance.yahoo.com"] * 2
    assert vues[0].url.raw_path.startswith(b"/v8/finance/chart/%5EGSPC?")
    assert vues[1].url.raw_path.startswith(b"/v8/finance/chart/EURUSD%3DX?")


@pytest.mark.parametrize(
    ("debut", "fin"),
    [("2024-1-2", "2024-01-04"), ("20240102", "2024-01-04"), ("2024-02-30", "2024-03-01"),
     ("2024-01-05", "2024-01-04"), ("2024-01-02", "demain"), ("", "")],
)  # fmt: skip
async def test_dates_are_iso_and_ordered(debut: str, fin: str) -> None:
    fournisseur = FakeMarketData()
    with pytest.raises(MarketDataError):
        await fournisseur.get_bars("AAPL", debut, fin)


async def test_executer_refuses_an_unexpected_argument_and_an_unknown_operation() -> None:
    fournisseur = FakeMarketData()
    with pytest.raises(MarketDataError, match="unexpected"):
        await fournisseur.executer(
            "get_bars", {"symbol": "A", "start": "2024-01-02", "end": "2024-01-03", "x": 1}
        )
    with pytest.raises(AdapterError, match="no operation"):
        await fournisseur.executer("delete_everything", {})


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
    reponse = await composite.get_bars("X", "2024-01-02", "2024-01-03")
    assert reponse["provider"] == "c" and reponse["cross_check"] is None
    assert [e["source"] for e in reponse["fallbacks"]] == ["a", "b"]
    assert d.calls == [], "without cross_check the later sources are not asked"


async def test_composite_cross_check_math() -> None:
    primaire = provider_with(
        [bar("2024-01-02", 100.0), bar("2024-01-03", 100.0), bar("2024-01-04", 100.0)], "a"
    )
    second = provider_with([bar("2024-01-03", 101.0), bar("2024-01-04", 110.0), bar("2024-01-05", 1.0)], "b")
    reponse = await CompositeProvider([("a", primaire), ("b", second)], tolerance=0.02).get_bars(
        "X", "2024-01-02", "2024-01-05"
    )
    assert reponse["provider"] == "a" and reponse["bars"][0]["date"] == "2024-01-02"
    controle = reponse["cross_check"]
    assert controle["source"] == "b" and controle["common_days"] == 2
    assert controle["max_rel_diff"] == pytest.approx(0.10)
    assert controle["diverging_days"] == 1 and controle["diverging_dates"] == ["2024-01-04"]


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
    reponse = await CompositeProvider([("a", a), ("b", b), ("c", c)]).get_bars(
        "X", "2024-01-02", "2024-01-03"
    )
    assert reponse["cross_check"]["source"] == "c" and reponse["cross_check"]["diverging_days"] == 0
    assert [e["source"] for e in reponse["fallbacks"]] == ["b"]


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
    seul = CompositeProvider([("stooq", stooq)])
    assert (await seul.get_instrument("AAPL"))["type"] == "unknown"


def test_registry_builds_the_family_and_the_composite_gets_its_secrets_by_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from choregos_adapters import build, spec_of

    # `CHOREGOS_FAKES=1` (set by the API's suite when everything runs together) would build the fake.
    monkeypatch.setenv("CHOREGOS_FAKES", "0")

    for nom in ("stooq", "yahoo", "alphavantage", "composite"):
        spec = spec_of("marketdata", nom)
        assert spec is not None and {o.name for o in spec.operations} == {"get_bars", "get_instrument"}
        assert {o.access for o in spec.operations} == {"read"}
    assert spec_of("marketdata", "stooq").secret_fields == ("api_key",)  # type: ignore[union-attr]
    composite = build(
        "marketdata",
        "composite",
        {"sources": ["stooq", "alphavantage"], "alphavantage_api_key": KEY, "stooq_api_key": KEY},
    )
    assert [nom for nom, _ in composite._sources] == ["stooq", "alphavantage"]
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
    autre = await FakeMarketData().get_bars("MSFT", "2024-01-01", "2024-03-31")
    assert autre["bars"] != a["bars"]
    for x in a["bars"]:
        assert x["low"] <= min(x["open"], x["close"]) <= max(x["open"], x["close"]) <= x["high"]
        assert isinstance(x["close"], float) and x["close"] > 0
    assert seed_of("aapl") == seed_of("AAPL")
    assert synthetic_bars("AAPL", date(2024, 1, 6), date(2024, 1, 7)) == [], "a weekend has no bar"


async def test_fake_scripting_fixtures_instruments_errors_and_calls() -> None:
    faux = FakeMarketData(
        fixtures={"FIX": [bar("2024-01-03", 2.0), bar("2024-01-02", 1.0), bar("2023-01-02", 9.0)]},
        instruments={"FIX": {"type": "etf", "name": "Fixture"}},
        errors={"BOOM": RateLimited("fake: slow down")},
    )
    reponse = await faux.get_bars("FIX", "2024-01-01", "2024-01-31")
    assert [b["close"] for b in reponse["bars"]] == [1.0, 2.0]
    assert (await faux.get_instrument("FIX"))["type"] == "etf"
    with pytest.raises(RateLimited):
        await faux.get_bars("BOOM", "2024-01-01", "2024-01-31")
    with pytest.raises(UnknownSymbol):
        await faux.get_instrument("NOPE1")
    assert [c[0] for c in faux.calls] == ["get_bars", "get_instrument", "get_bars", "get_instrument"]
