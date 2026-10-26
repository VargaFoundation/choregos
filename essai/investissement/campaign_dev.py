# SPDX-License-Identifier: Apache-2.0
"""S38-13 on the dev tenant: a real campaign of `investment-research`, a real model, real free data.

    uv run python essai/investissement/campaign_dev.py prepare   # org connector, project, settings
    uv run python essai/investissement/campaign_dev.py open      # one campaign of UNIVERSE
    uv run python essai/investissement/campaign_dev.py follow ID # state, runs, facts, cost, artifacts

Variables:
    CHOREGOS_DEV_URL         the tenant's console (e.g. `https://choregos.example`)
    CHOREGOS_DEV_TOKEN_FILE  a file holding an administrator's API token (`chg_…`); never printed
    CHOREGOS_DEV_ORG         (default varga)
    CHOREGOS_DEV_PROJECT     (default research)

The Alpha Vantage key is not here: the tenant's pods read it from their secret, and the org
connector names it (`env:CONNECTOR_ALPHAVANTAGE_API_KEY`).
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
from typing import Any

import httpx

URL = os.environ.get("CHOREGOS_DEV_URL", "").rstrip("/")
ORG = os.environ.get("CHOREGOS_DEV_ORG", "varga")
PROJECT = os.environ.get("CHOREGOS_DEV_PROJECT", "research")
#: Twenty US products: broad, size, region, rates, commodities, sectors, and four single stocks.
UNIVERSE = [
    "SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "GLD", "SLV", "USO",
    "VNQ", "XLE", "XLF", "XLK", "XLV", "XLU", "AAPL", "MSFT", "JNJ", "XOM",
]  # fmt: skip
#: The out-of-sample period starts after the training cut-off of the models the gateway serves.
FIELDS: dict[str, Any] = {
    "symbols": UNIVERSE,
    "start": "2019-01-02",
    "oos_start": "2025-07-01",
    "end": "2026-10-16",
    "max_strategies": 6,
}


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        sys.exit(f"missing variable: {name} (see this script's header)")
    return value


def client() -> httpx.Client:
    token = pathlib.Path(required("CHOREGOS_DEV_TOKEN_FILE")).read_text(encoding="utf-8").strip()
    base = required("CHOREGOS_DEV_URL").rstrip("/")
    return httpx.Client(base_url=f"{base}/api/v1", headers={"Authorization": f"Bearer {token}"}, timeout=60)


def read(response: httpx.Response, *expected: int) -> Any:
    if response.status_code not in expected:
        request = response.request
        sys.exit(f"{request.method} {request.url.path} → {response.status_code}: {response.text[:400]}")
    return response.json() if response.content else None


def _project(http: httpx.Client) -> dict[str, Any]:
    return dict(read(http.get(f"/projects/{ORG}:{PROJECT}"), 200))


def prepare() -> None:
    with client() as http:
        connectors = read(http.get(f"/orgs/{ORG}/connectors"), 200)
        names = {c["name"] for c in (connectors if isinstance(connectors, list) else connectors["items"])}
        if "marketdata" not in names:
            body = {
                "name": "marketdata",
                "kind": "marketdata",
                "type": "composite",
                # A free Alpha Vantage key refuses a multi-year series: in `compact` it checks the last
                # 100 trading days of Yahoo's (S38-21).
                "config": {
                    "sources": ["yahoo", "alphavantage"],
                    "source_configs": {"alphavantage": {"outputsize": "compact"}},
                    "cross_check": True,
                    "tolerance": 0.02,
                },
                "secret_refs": {"alphavantage_api_key": "env:CONNECTOR_ALPHAVANTAGE_API_KEY"},
            }
            read(http.post(f"/orgs/{ORG}/connectors", json=body), 201)
        projects = {p["slug"] for p in read(http.get(f"/orgs/{ORG}/projects"), 200)["items"]}
        if PROJECT not in projects:
            body = {
                "slug": PROJECT,
                "name": "Investment research",
                "template_ref": "investment-research",
                "config": {"slug": PROJECT, "org": ORG},
            }
            read(http.post(f"/orgs/{ORG}/projects", json=body), 201)
        project = _project(http)
        read(
            http.put(
                f"/projects/{project['id']}/connectors/tracker", json={"type": "internal", "config": {}}
            ),
            200,
        )
        # The backend and the profiles the dev tenant serves (a new project gets neither).
        config = {
            **(project.get("config") or {}),
            "agent": {"default_backend": "opencode", "allowed_backends": ["opencode"]},
            "models": {"profiles": {"standard": "platform/standard", "cheap": "platform/cheap"}},
        }
        read(http.patch(f"/projects/{project['id']}", json={"config": config}), 200)
        for line in read(http.get(f"/projects/{project['id']}/requirements"), 200):
            connector = line.get("connector") or {}
            reason = (line.get("reasons") or [""])[0]
            print(f"{line['capability']:12s} {connector.get('type') or 'MISSING':12s} {reason}")
        print(f"project {PROJECT}: {URL}/p/{PROJECT}")


def open_campaign() -> None:
    with client() as http:
        project = _project(http)
        body = {
            "title": f"Campaign: {len(UNIVERSE)} US products, OOS from {FIELDS['oos_start']}",
            "fields": FIELDS,
        }
        item = read(http.post(f"/projects/{project['id']}/work-items", json=body), 201)
        print(json.dumps({k: item.get(k) for k in ("id", "tracker_key", "workflow_name", "state")}))


def follow(item_id: str) -> None:
    with client() as http:
        item = read(http.get(f"/work-items/{item_id}"), 200)
        print(json.dumps({k: item.get(k) for k in ("tracker_key", "state", "totals")}, default=str))
        runs = read(http.get(f"/work-items/{item_id}/runs"), 200)
        for run in runs if isinstance(runs, list) else runs.get("items", []):
            keep = (
                "transition_id",
                "stage_role",
                "attempt",
                "status",
                "cost_usd",
                "model",
                "started_at",
                "ended_at",
            )
            print(json.dumps({k: run.get(k) for k in keep}, default=str))
        artifacts = read(http.get(f"/work-items/{item_id}/artifacts"), 200)
        for artifact in artifacts if isinstance(artifacts, list) else artifacts.get("items", []):
            print(json.dumps({k: artifact.get(k) for k in ("name", "type", "size", "sha256")}))


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "prepare":
        prepare()
    elif command == "open":
        open_campaign()
    elif command == "follow" and len(sys.argv) > 2:
        follow(sys.argv[2])
    else:
        sys.exit(__doc__)
