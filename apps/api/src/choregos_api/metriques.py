"""Les métriques Prometheus de la plateforme — calculées depuis la base, exposées par l'API.

POURQUOI DEPUIS LA BASE
-----------------------
Le chart livrait un ServiceMonitor, quatre alertes et six tableaux de bord qui lisaient
seize séries `choregos_*` qu'aucun processus n'émettait (état des lieux du 2026-09-24). La
voie classique — des compteurs en mémoire dans chaque processus — aurait donné des chiffres
par réplique, à recoller, et aurait exigé un serveur HTTP dans l'orchestrateur. Ici, tout
ce qui compte (runs, coûts, tickets, refus) est DÉJÀ dans la base : une seule vérité, la
même sur chaque réplique de l'API, rafraîchie toutes les quelques secondes et servie
sur `/metrics`. Seul `http_requests_total` est un vrai compteur de processus.

CE QUE ÇA NE FAIT PAS
---------------------
Pas de traces. Ce module donne les séries que les tableaux de bord lisent ; l'inventaire
`NOMS` est ce qu'un test compare aux tableaux de bord et aux alertes, pour qu'un panneau
ne cite plus jamais une métrique qui n'existe pas.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections import Counter, defaultdict
from collections.abc import Iterable
from datetime import timedelta
from typing import Any

from choregos_core import utcnow
from prometheus_client import CollectorRegistry, generate_latest
from prometheus_client import Counter as CompteurProm
from prometheus_client.core import GaugeMetricFamily, HistogramMetricFamily
from prometheus_client.registry import Collector
from sqlalchemy import func, select

from .logging import get_logger

logger = get_logger("choregos.metriques")

#: L'inventaire : ce que `/metrics` sait produire. Le test des tableaux de bord s'y réfère.
NOMS = frozenset(
    {
        "http_requests_total",
        "choregos_runs_active",
        "choregos_runs_total",
        "choregos_run_start_seconds",
        "choregos_cost_usd_total",
        "choregos_project_daily_cost_usd",
        "choregos_project_daily_budget_usd",
        "choregos_first_pass_merge_rate",
        "choregos_releases_total",
        "choregos_lead_time_seconds",
        "choregos_train_frozen",
        "choregos_train_status",
        "choregos_permission_denials_total",
        "choregos_memory_pending",
        "choregos_context_pack_empty_total",
    }
)

SECONDES_DEMARRAGE = (5.0, 15.0, 30.0, 60.0, 120.0, 300.0, 600.0, 1800.0)
SECONDES_LIVRAISON = (3600.0, 4 * 3600.0, 8 * 3600.0, 24 * 3600.0, 3 * 86400.0, 7 * 86400.0, 30 * 86400.0)
ENVIRONNEMENTS = ("dev", "staging", "prod")
#: Les tableaux de bord affichent l'état d'un train comme un nombre.
ETATS_TRAIN = {
    "collecting": 0,
    "departing": 1,
    "staged": 2,
    "awaiting_approval": 3,
    "promoting": 4,
    "verifying": 5,
}

registre = CollectorRegistry()
requetes_http = CompteurProm(
    "http_requests_total",
    "Requêtes HTTP reçues par l'API",
    ["method", "route", "status"],
    registry=registre,
)


def _buckets(valeurs: Iterable[float], bornes: tuple[float, ...]) -> tuple[list[tuple[str, float]], float]:
    """Comptes cumulés par borne, comme Prometheus les attend, et la somme."""
    liste = list(valeurs)
    cumul = [(str(b), float(sum(1 for v in liste if v <= b))) for b in bornes]
    cumul.append(("+Inf", float(len(liste))))
    return cumul, float(sum(liste))


class Instantane:
    """Ce que la base disait au dernier rafraîchissement. Vide tant qu'aucun n'a eu lieu."""

    def __init__(self) -> None:
        self.runs_active = 0.0
        self.runs_total: dict[str, float] = {}
        self.run_start: tuple[list[tuple[str, float]], float] = ([], 0.0)
        self.cost: dict[tuple[str, str, str], float] = {}
        self.daily_cost: dict[str, float] = {}
        self.daily_budget: dict[str, float] = {}
        self.first_pass: dict[str, float] = {}
        self.releases: dict[str, float] = {}
        self.lead_time: tuple[list[tuple[str, float]], float] = ([], 0.0)
        self.train_frozen: dict[tuple[str, str], float] = {}
        self.train_status: dict[tuple[str, str], float] = {}
        self.denials: dict[str, float] = {}
        self.memory_pending = 0.0
        self.context_pack_empty: dict[str, float] = {}
        self.rafraichi_a: float | None = None


class _Courant:
    """Le dernier instantané. Un porteur plutôt qu'un `global` : on le remplace d'un bloc."""

    instantane = Instantane()


class CollecteurPlateforme(Collector):
    """Rend l'instantané sous forme de séries. Sans effet de bord, sans base : la lecture est instantanée."""

    def collect(self) -> Iterable[Any]:
        s = _Courant.instantane
        yield GaugeMetricFamily("choregos_runs_active", "Runs en file ou en cours", value=s.runs_active)
        g = GaugeMetricFamily("choregos_runs_total", "Runs par état final", labels=["status"])
        for status, n in s.runs_total.items():
            g.add_metric([status], n)
        yield g
        yield HistogramMetricFamily(
            "choregos_run_start_seconds",
            "De la création d'un run à son démarrage (30 derniers jours)",
            buckets=s.run_start[0],
            sum_value=s.run_start[1],
        )
        g = GaugeMetricFamily(
            "choregos_cost_usd_total", "Dépense au registre, USD", labels=["project", "stage", "model"]
        )
        for (projet, stage, modele), v in s.cost.items():
            g.add_metric([projet, stage, modele], v)
        yield g
        g = GaugeMetricFamily(
            "choregos_project_daily_cost_usd", "Dépense du jour (UTC) par projet", labels=["project"]
        )
        for projet, v in s.daily_cost.items():
            g.add_metric([projet], v)
        yield g
        g = GaugeMetricFamily(
            "choregos_project_daily_budget_usd", "Budget journalier de la politique", labels=["project"]
        )
        for projet, v in s.daily_budget.items():
            g.add_metric([projet], v)
        yield g
        g = GaugeMetricFamily(
            "choregos_first_pass_merge_rate", "Part des PR fusionnées au premier passage", labels=["project"]
        )
        for projet, v in s.first_pass.items():
            g.add_metric([projet], v)
        yield g
        g = GaugeMetricFamily("choregos_releases_total", "Releases par état", labels=["status"])
        for status, n in s.releases.items():
            g.add_metric([status], n)
        yield g
        yield HistogramMetricFamily(
            "choregos_lead_time_seconds",
            "De la création d'un ticket à sa clôture (90 derniers jours)",
            buckets=s.lead_time[0],
            sum_value=s.lead_time[1],
        )
        g = GaugeMetricFamily("choregos_train_frozen", "1 si le train est gelé", labels=["project", "env"])
        for (projet, env), v in s.train_frozen.items():
            g.add_metric([projet, env], v)
        yield g
        g = GaugeMetricFamily(
            "choregos_train_status", "État du train (0 collecte … 5 vérification)", labels=["project", "env"]
        )
        for (projet, env), v in s.train_status.items():
            g.add_metric([projet, env], v)
        yield g
        g = GaugeMetricFamily(
            "choregos_permission_denials_total", "Demandes de permission refusées", labels=["project"]
        )
        for projet, v in s.denials.items():
            g.add_metric([projet], v)
        yield g
        yield GaugeMetricFamily(
            "choregos_memory_pending", "Faits de mémoire en attente de validation", value=s.memory_pending
        )
        g = GaugeMetricFamily(
            "choregos_context_pack_empty_total", "Runs partis avec un context pack vide", labels=["project"]
        )
        for projet, v in s.context_pack_empty.items():
            g.add_metric([projet], v)
        yield g


registre.register(CollecteurPlateforme())


def exposer() -> bytes:
    return generate_latest(registre)


async def rafraichir() -> Instantane:
    """Recalcule l'instantané depuis la base. Une passe : une dizaine de requêtes bornées."""
    from .db.models import CostLedger, MemoryFact, Project, Release, Run, RunEvent, WorkItem
    from .db.session import session_scope
    from .services import active_policy, first_pass_merge_rate, policy_model

    maintenant = utcnow()
    nouveau = Instantane()
    async with session_scope(orgs="*") as session:
        projets = {p.id: p for p in (await session.execute(select(Project))).scalars()}
        slug = {pid: p.slug for pid, p in projets.items()}

        actifs = await session.execute(
            select(func.count()).select_from(Run).where(Run.status.in_(["queued", "running"]))
        )
        nouveau.runs_active = float(actifs.scalar_one())
        par_statut = await session.execute(select(Run.status, func.count()).group_by(Run.status))
        nouveau.runs_total = {str(st): float(n) for st, n in par_statut.all()}

        depuis = maintenant - timedelta(days=30)
        demarres = await session.execute(
            select(Run.created_at, Run.started_at).where(
                Run.started_at.is_not(None), Run.created_at >= depuis
            )
        )
        durees = [max(0.0, (_aware(s) - _aware(c)).total_seconds()) for c, s in demarres.all() if s and c]
        nouveau.run_start = _buckets(durees, SECONDES_DEMARRAGE)

        couts = await session.execute(
            select(
                CostLedger.project_id, CostLedger.stage_role, CostLedger.model, func.sum(CostLedger.cost_usd)
            ).group_by(CostLedger.project_id, CostLedger.stage_role, CostLedger.model)
        )
        nouveau.cost = {
            (slug.get(pid, "?"), str(stage or ""), str(modele or "")): float(total or 0.0)
            for pid, stage, modele, total in couts.all()
        }
        minuit = maintenant.replace(hour=0, minute=0, second=0, microsecond=0)
        jour = await session.execute(
            select(CostLedger.project_id, func.sum(CostLedger.cost_usd))
            .where(CostLedger.ts >= minuit)
            .group_by(CostLedger.project_id)
        )
        nouveau.daily_cost = {slug.get(pid, "?"): float(total or 0.0) for pid, total in jour.all()}
        for pid, projet in projets.items():
            politique = policy_model(await active_policy(session, pid))
            budget = politique.budgets.daily_project_usd
            if budget:
                nouveau.daily_budget[projet.slug] = float(budget)
            taux = await first_pass_merge_rate(session, pid)
            if taux is not None:
                nouveau.first_pass[projet.slug] = float(taux)

        releases = await session.execute(select(Release.status, func.count()).group_by(Release.status))
        nouveau.releases = {str(st): float(n) for st, n in releases.all()}

        clos = await session.execute(
            select(WorkItem.created_at, WorkItem.closed_at).where(
                WorkItem.closed_at.is_not(None), WorkItem.closed_at >= maintenant - timedelta(days=90)
            )
        )
        nouveau.lead_time = _buckets(
            [max(0.0, (_aware(f) - _aware(c)).total_seconds()) for c, f in clos.all() if f and c],
            SECONDES_LIVRAISON,
        )

        refus = await session.execute(
            select(Run.project_id, func.count())
            .select_from(RunEvent)
            .join(Run, Run.id == RunEvent.run_id)
            .where(
                RunEvent.type == "session/request_permission",
                RunEvent.payload["allowed"].as_boolean().is_(False),
            )
            .group_by(Run.project_id)
        )
        nouveau.denials = {slug.get(pid, "?"): float(n) for pid, n in refus.all()}

        attente = await session.execute(
            select(func.count()).select_from(MemoryFact).where(MemoryFact.status == "pending")
        )
        nouveau.memory_pending = float(attente.scalar_one())

        vides: Counter[str] = Counter()
        packs = await session.execute(
            select(Run.project_id, Run.context_pack).where(Run.created_at >= depuis)
        )
        for pid, pack in packs.all():
            if pack is not None and not (pack.get("facts") or pack.get("items") or pack.get("snippets")):
                vides[slug.get(pid, "?")] += 1
        nouveau.context_pack_empty = {k: float(v) for k, v in vides.items()}

    nouveau.train_frozen, nouveau.train_status = await _trains(list(slug.values()))
    nouveau.rafraichi_a = maintenant.timestamp()
    _Courant.instantane = nouveau
    return nouveau


async def _trains(slugs: list[str]) -> tuple[dict[tuple[str, str], float], dict[tuple[str, str], float]]:
    """L'état des trains vit dans Temporal : une requête `status` par (projet, environnement), bornée."""
    from .temporal import get_temporal, train_id

    geles: dict[tuple[str, str], float] = {}
    etats: dict[tuple[str, str], float] = {}
    temporal = get_temporal()
    for projet in slugs:
        for env in ENVIRONNEMENTS:
            try:
                live = await asyncio.wait_for(temporal.query(train_id(projet, env), "status"), timeout=2.0)
            except Exception as exc:  # pas de train, ou Temporal lent : la série manque, l'API vit
                logger.debug("train sans état", projet=projet, env=env, error=str(exc)[:80])
                continue
            if not isinstance(live, dict):
                continue
            geles[(projet, env)] = 1.0 if live.get("frozen") else 0.0
            etats[(projet, env)] = float(ETATS_TRAIN.get(str(live.get("state", "")), -1))
    return geles, etats


def _aware(moment: Any) -> Any:
    from datetime import UTC

    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def boucle_de_rafraichissement(intervalle_s: float) -> None:
    """Tâche de fond : un rafraîchissement raté ne tue pas l'API, il se voit dans `rafraichi_a`."""
    while True:
        try:
            await rafraichir()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("rafraîchissement des métriques raté", error=str(exc)[:200])
        await asyncio.sleep(intervalle_s)


def _stub_for_type_checkers() -> None:  # pragma: no cover
    _ = defaultdict, contextlib
