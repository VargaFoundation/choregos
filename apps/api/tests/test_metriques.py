"""Les métriques existent, et les tableaux de bord ne citent que ce qui existe.

Avant le 2026-09-24 : un ServiceMonitor, quatre alertes et six tableaux de bord lisaient
seize séries `choregos_*` qu'aucun processus n'émettait. Six tableaux vides, livrés comme
s'ils marchaient. Le premier test empêche que ça revienne ; le second vérifie que
`/metrics` porte les séries, calculées depuis la base.
"""

from __future__ import annotations

import json
import pathlib
import re
from typing import Any

from httpx import AsyncClient

RACINE = pathlib.Path(__file__).resolve().parents[3]
DASHBOARDS = RACINE / "charts" / "choregos" / "dashboards"
ALERTES = RACINE / "charts" / "choregos" / "templates" / "monitoring.yaml"
CHOREGOS = re.compile(r"choregos_[a-z_]+")
SUFFIXES = ("_bucket", "_count", "_sum")


def _base(nom: str) -> str:
    for suffixe in SUFFIXES:
        if nom.endswith(suffixe):
            return nom[: -len(suffixe)]
    return nom


def test_les_tableaux_de_bord_et_les_alertes_ne_citent_que_des_metriques_emises() -> None:
    from choregos_api.metriques import NOMS

    citees: dict[str, set[str]] = {}
    for fichier in sorted(DASHBOARDS.glob("*.json")):
        texte = json.dumps(json.loads(fichier.read_text()))
        citees[fichier.name] = {_base(m) for m in CHOREGOS.findall(texte)}
    citees["monitoring.yaml"] = {_base(m) for m in CHOREGOS.findall(ALERTES.read_text())}
    absentes = {f: sorted(noms - NOMS) for f, noms in citees.items() if noms - NOMS}
    assert not absentes, f"métriques citées mais jamais émises : {absentes}"
    assert sum(len(n) for n in citees.values()) >= 12, "les tableaux de bord doivent encore lire des séries"


async def test_metrics_expose_les_series_depuis_la_base(client: AsyncClient, project: dict[str, Any]) -> None:
    from choregos_api.db.models import CostLedger, Run, WorkItem
    from choregos_api.db.session import session_scope
    from choregos_api.metriques import NOMS, rafraichir
    from choregos_core import utcnow

    async with session_scope() as session:
        item = WorkItem(project_id=project["id"], tracker_key="varga/billing-api#9", title="T", state="ready")
        session.add(item)
        await session.flush()
        session.add(
            Run(
                id="varga-billing-api-9-t-implement-1",
                work_item_id=item.id,
                project_id=project["id"],
                transition_id="t-implement",
                stage_role="implement",
                attempt=1,
                actor="agent",
                backend="claude-code",
                model="platform/standard",
                status="running",
                started_at=utcnow(),
                stage_input={},
            )
        )
        session.add(
            CostLedger(
                project_id=project["id"],
                work_item_id=item.id,
                run_id=None,
                provider="anthropic",
                model="platform/standard",
                backend="claude-code",
                stage_role="implement",
                size="M",
                tokens_in=10,
                tokens_out=5,
                tokens_cached=0,
                cost_usd=0.5,
                cost_eur=0.46,
                fx_rate=0.92,
                ts=utcnow(),
                kind="model",
            )
        )
    await rafraichir()
    reponse = await client.get("/metrics")
    assert reponse.status_code == 200
    corps = reponse.text
    for nom in NOMS:
        assert nom in corps, f"série absente de /metrics : {nom}"
    assert "choregos_runs_active 1.0" in corps
    assert (
        'choregos_cost_usd_total{model="platform/standard",project="billing-api",stage="implement"} 0.5'
        in corps
    )
    assert 'choregos_project_daily_cost_usd{project="billing-api"} 0.5' in corps
    assert "http_requests_total{" in corps


async def test_chaque_reponse_porte_un_identifiant_de_requete(client: AsyncClient) -> None:
    """Le journal de l'API et celui du front se relient par cet identifiant."""
    sans = await client.get("/healthz")
    assert len(sans.headers["x-request-id"]) >= 12
    avec = await client.get("/healthz", headers={"X-Request-Id": "req-du-front-42"})
    assert avec.headers["x-request-id"] == "req-du-front-42"
