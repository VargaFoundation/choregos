# SPDX-License-Identifier: Apache-2.0
"""Static HTML report: no script, no external resource."""

from __future__ import annotations

from html import escape
from typing import Any

from choregos_quant.campaign import Campaign

DISCLAIMER = (
    "Research note for internal use. Not investment advice. Past performance does not predict future results."
)
_STYLE = (
    "body{font-family:sans-serif;margin:2rem;color:#111}table{border-collapse:collapse;margin:1rem 0}"
    "th,td{border:1px solid #bbb;padding:.25rem .6rem;text-align:right}th:first-child,td:first-child,"
    "th:nth-child(2),td:nth-child(2){text-align:left}.yes{background:#dff3df}.note{color:#444;font-size:.9rem}"
)


def _f(x: float) -> str:
    return f"{x:.4f}"


def render_report(doc: dict[str, Any], campaign: Campaign) -> str:
    facts = doc["facts"]
    rows = []
    for symbol in sorted(doc["products"]):
        prod = doc["products"][symbol]
        best = prod["best_in_sample"]
        e = prod["strategies"][best]
        bh = prod["strategies"]["buy_and_hold"]["oos"]
        cls = ' class="yes"' if prod["relevant"] else ""
        rows.append(
            f"<tr{cls}><td>{escape(symbol)}</td><td>{escape(best)}</td><td>{_f(e['in_sample']['sharpe'])}</td>"
            f"<td>{_f(e['oos']['sharpe'])}</td><td>{_f(e['oos']['dsr'])}</td>"
            f"<td>{_f(e['oos']['max_drawdown'])}</td><td>{int(e['oos']['trades'])}</td>"
            f"<td>{_f(e['oos']['cagr'])}</td><td>{_f(bh['cagr'])}</td>"
            f"<td>{'yes' if prod['relevant'] else 'no'}</td></tr>"
        )
    detail = []
    for symbol in sorted(doc["products"]):
        for sid, e in sorted(doc["products"][symbol]["strategies"].items()):
            o, i = e["oos"], e["in_sample"]
            dsr = _f(o["dsr"]) if "dsr" in o else ""
            detail.append(
                f"<tr><td>{escape(symbol)}</td><td>{escape(sid)}</td><td>{_f(i['sharpe'])}</td>"
                f"<td>{_f(o['sharpe'])}</td><td>{dsr}</td><td>{_f(o['max_drawdown'])}</td>"
                f"<td>{int(o['trades'])}</td><td>{_f(o['cagr'])}</td></tr>"
            )
    head = (
        "<tr><th>Product</th><th>Best strategy (in-sample)</th><th>IS Sharpe</th><th>OOS Sharpe</th>"
        "<th>OOS DSR</th><th>OOS max drawdown</th><th>OOS trades</th><th>OOS CAGR</th>"
        "<th>Buy-and-hold OOS CAGR</th><th>Relevant</th></tr>"
    )
    head2 = (
        "<tr><th>Product</th><th>Strategy</th><th>IS Sharpe</th><th>OOS Sharpe</th><th>OOS DSR</th>"
        "<th>OOS max drawdown</th><th>OOS trades</th><th>OOS CAGR</th></tr>"
    )
    return (
        '<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>Backtest report</title>'
        f"<style>{_STYLE}</style></head><body>\n<h1>Backtest report</h1>\n"
        f'<p class="note">{escape(DISCLAIMER)}</p>\n'
        f"<p>Products: {facts['products']} - trials: {facts['trials']} - "
        f"relevant: {facts['relevant_count']} - "
        f"out-of-sample from {escape(campaign.oos_start)} - "
        f"cost {campaign.cost_bps:g} bps per position change - "
        f"gates: DSR &ge; {campaign.min_dsr:g}, max drawdown &le; {campaign.max_drawdown:g}, "
        f"trades &ge; {campaign.min_trades}.</p>\n"
        f"<h2>Best strategy per product</h2>\n<table>{head}{''.join(rows)}</table>\n"
        f"<h2>All strategies</h2>\n<table>{head2}{''.join(detail)}</table>\n"
        f'<p class="note">bars sha256 {escape(str(facts["bars_sha256"]))}<br>'
        f"strategies sha256 {escape(str(facts['strategies_sha256']))}</p>\n"
        f'<p class="note">{escape(DISCLAIMER)}</p>\n</body></html>\n'
    )
