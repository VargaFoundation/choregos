"""GitHub facts for a catalogue: stars, licence, archived (ADR 0065).

One GraphQL query per batch of repositories, through the recording client of `_recording`, so a
replayed run reads the same answers the maintainer recorded. The token (`GITHUB_TOKEN`) is only
needed with `--record`; it is an `Authorization` header, which no recording stores.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

import httpx

GRAPHQL_URL = "https://api.github.com/graphql"
BATCH = 80
_REPO_URL = re.compile(r"^https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$")


@dataclass(frozen=True)
class RepoFacts:
    """What the catalogue keeps from a repository."""

    full_name: str
    stars: int
    archived: bool
    licence: str | None  # an SPDX id, `None` when GitHub detects none (or `NOASSERTION`)


def parse_repo(value: str) -> tuple[str, str] | None:
    """`owner/name` or a github.com URL → (owner, name); anything else → `None`."""
    candidate = value.strip()
    match = _REPO_URL.match(candidate)
    if match:
        return match.group(1), match.group(2)
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", candidate):
        owner, _, name = candidate.partition("/")
        return owner, name
    return None


def _query(repos: list[tuple[str, str]]) -> str:
    parts = [
        f"r{i}: repository(owner: {json.dumps(owner)}, name: {json.dumps(name)}) "
        "{ nameWithOwner stargazerCount isArchived licenseInfo { spdxId } }"
        for i, (owner, name) in enumerate(repos)
    ]
    return "query {\n  " + "\n  ".join(parts) + "\n}"


def fetch_repo_facts(client: httpx.Client, repos: list[str]) -> dict[str, RepoFacts | None]:
    """Facts for each `owner/name`, `None` for a repository GitHub does not know (renamed away, deleted).

    The batches are sorted so the request bodies, hence the recordings, are stable.
    """
    parsed = sorted({p for r in repos if (p := parse_repo(r))})
    result: dict[str, RepoFacts | None] = {}
    for start in range(0, len(parsed), BATCH):
        batch = parsed[start : start + BATCH]
        response = client.post(GRAPHQL_URL, json={"query": _query(batch)})
        response.raise_for_status()
        payload: dict[str, Any] = response.json()
        data = payload.get("data") or {}
        for i, (owner, name) in enumerate(batch):
            node = data.get(f"r{i}")
            key = f"{owner}/{name}"
            if not node:
                result[key] = None
                continue
            spdx = (node.get("licenseInfo") or {}).get("spdxId")
            result[key] = RepoFacts(
                full_name=node["nameWithOwner"],
                stars=int(node["stargazerCount"]),
                archived=bool(node["isArchived"]),
                licence=None if spdx in (None, "NOASSERTION") else spdx,
            )
    return result
