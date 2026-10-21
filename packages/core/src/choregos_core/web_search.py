# SPDX-License-Identifier: Apache-2.0
"""What a web search backend does with the queries, and whether an organisation accepts it (ADR 0083).

Every backend declares its data handling in code (`choregos_adapters.web_search`): how long it keeps a
query, whether it reuses queries (to train, rank or re-index), where it processes them, and the
document the declaration comes from. An organisation may only make a declaration **stricter** — an
enterprise contract granting zero retention, say — and must then name that contract (`terms_ref`).

`None` means unknown, and an unknown value fails every bound that reads it: a backend that does not say
how long it keeps queries is not used under a retention cap.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import Any

from choregos_contracts.policy import WebSearchPolicy

from .policy_ceiling import CeilingWebSearch


@dataclass(frozen=True)
class DataHandling:
    #: Days a query is kept by the backend; 0: not kept; `None`: unknown.
    query_retention_days: int | None = None
    #: Queries used to train, rank or re-index; `None`: unknown.
    query_reuse: bool | None = None
    #: Where queries are processed (`eu`, `us`, `global`...); `None`: unknown.
    region: str | None = None
    #: The document the declaration comes from (a dated URL, a contract reference).
    terms_ref: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FIELDS = ("query_retention_days", "query_reuse", "region", "terms_ref")


def stricter(declared: DataHandling, override: Mapping[str, Any] | None) -> DataHandling:
    """The backend's declaration with the organisation's override applied. Raises `ValueError` when the
    override widens it or does not say where it comes from. A `None` in the override changes nothing."""
    if not override:
        return declared
    unknown = sorted(set(override) - set(_FIELDS))
    if unknown:
        raise ValueError(f"data_handling: unknown field(s) {unknown}")
    given = {k: v for k, v in override.items() if v is not None}
    if not given:
        return declared
    terms = given.get("terms_ref")
    if not isinstance(terms, str) or not terms.strip():
        raise ValueError("data_handling: an override must name the terms it comes from (`terms_ref`)")
    retention = given.get("query_retention_days", declared.query_retention_days)
    if "query_retention_days" in given:
        if not isinstance(retention, int) or isinstance(retention, bool) or retention < 0:
            raise ValueError("data_handling.query_retention_days: a number of days, 0 or more")
        if declared.query_retention_days is not None and retention > declared.query_retention_days:
            raise ValueError(
                f"data_handling.query_retention_days: {retention} is longer than the backend's "
                f"declared {declared.query_retention_days}: an override can only be stricter"
            )
    reuse = given.get("query_reuse", declared.query_reuse)
    if "query_reuse" in given:
        if not isinstance(reuse, bool):
            raise ValueError("data_handling.query_reuse: true or false")
        if reuse and declared.query_reuse is False:
            raise ValueError("data_handling.query_reuse: the backend declares no reuse; true is wider")
    region = given.get("region", declared.region)
    if "region" in given:
        if not isinstance(region, str) or not region.strip():
            raise ValueError("data_handling.region: a region name")
        if declared.region is not None and region != declared.region:
            raise ValueError(
                f"data_handling.region: the backend declares `{declared.region}`; only an unknown "
                "region can be declared by the organisation"
            )
    return DataHandling(
        query_retention_days=retention,
        query_reuse=reuse,
        region=region,
        terms_ref=terms.strip(),
    )


def shortfalls(
    backend: str,
    handling: DataHandling,
    ceiling: CeilingWebSearch | None,
    policy: WebSearchPolicy,
) -> list[str]:
    """Why this backend may not serve `web_search` for this project; empty: it may. The tighter of the
    organisation's ceiling and the project's policy applies."""
    if ceiling is None:
        return ["the organisation's ceiling does not open web search"]
    reasons: list[str] = []
    if ceiling.mode != "allow":
        reasons.append("the organisation's ceiling switches web search off")
    if policy.mode != "allow":
        reasons.append("the project's policy switches web search off")
    if ceiling.backends is not None and backend not in ceiling.backends:
        reasons.append(f"the backend `{backend}` is not among the organisation's {sorted(ceiling.backends)}")
    caps = [c for c in (ceiling.max_query_retention_days, policy.max_query_retention_days) if c is not None]
    if caps:
        cap = min(caps)
        if handling.query_retention_days is None:
            reasons.append(f"the backend does not say how long it keeps queries (cap: {cap} days)")
        elif handling.query_retention_days > cap:
            reasons.append(
                f"the backend keeps queries {handling.query_retention_days} days (cap: {cap} days)"
            )
    if not (ceiling.allow_query_reuse and policy.allow_query_reuse) and handling.query_reuse is not False:
        said = "does not say whether it reuses" if handling.query_reuse is None else "reuses"
        reasons.append(f"the backend {said} queries, and reuse is not allowed")
    if ceiling.regions is not None and handling.region not in ceiling.regions:
        shown = "an unknown region" if handling.region is None else f"`{handling.region}`"
        reasons.append(f"the backend processes queries in {shown} (allowed: {sorted(ceiling.regions)})")
    return reasons


__all__ = ["DataHandling", "shortfalls", "stricter"]
