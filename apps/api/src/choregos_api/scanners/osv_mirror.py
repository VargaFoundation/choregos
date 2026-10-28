# SPDX-License-Identifier: Apache-2.0
"""A local mirror of OSV for PyPI and npm, answering the two calls of OSV's API (ADR 0095).

The skill scanner must not call `api.osv.dev` (the owner's rule of 2026-10-26: no external
dependency at scan time, a local cache of the external resource at most). OSV publishes each
ecosystem as one zip of records (`<base>/<ecosystem>/all.zip`); `build` turns those zips into one
SQLite file, and `query_batch` / `vuln` answer `POST /v1/querybatch` and `GET /v1/vulns/{id}` from
it, matching versions here since OSV's API matches them server-side.

Standard library only, plus `packaging` when it is there: this module also runs inside the
scanner's own environment, beside a third-party tool, with nothing of the platform imported.
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

#: The ecosystems the scanner asks about (SkillSpector's SC4: requirements, pyproject, lockfiles,
#: package.json and its locks).
ECOSYSTEMS = ("PyPI", "npm")
#: Where OSV publishes them; an operator mirror can serve the same layout.
DEFAULT_BASE_URL = "https://osv-vulnerabilities.storage.googleapis.com"

_SCHEMA = """
CREATE TABLE vulns (id TEXT PRIMARY KEY, modified TEXT, record TEXT NOT NULL);
CREATE TABLE affected (ecosystem TEXT NOT NULL, name TEXT NOT NULL, vuln_id TEXT NOT NULL);
CREATE INDEX affected_package ON affected (ecosystem, name);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def normalize(ecosystem: str, name: str) -> str:
    """The name a package is looked up by: PEP 503 for PyPI, lower case for npm."""
    if ecosystem == "PyPI":
        return re.sub(r"[-_.]+", "-", name).lower()
    return name.lower()


# ───────────────────────── building ─────────────────────────


def _records(archive: Path) -> Iterator[dict[str, Any]]:
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.filename.endswith(".json"):
                with zf.open(info) as handle:
                    yield json.load(handle)


def build(archives: dict[str, Path], target: Path, *, built_at: float | None = None) -> int:
    """Writes the SQLite mirror of `archives` (`{ecosystem: all.zip}`) to `target`, atomically.

    Returns the number of records. A record that names no package of these ecosystems is kept out.
    """
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.unlink(missing_ok=True)
    count = 0
    with sqlite3.connect(tmp) as db:
        db.executescript(_SCHEMA)
        for ecosystem, archive in archives.items():
            for record in _records(archive):
                vuln_id = record.get("id")
                names = {
                    normalize(ecosystem, str(a.get("package", {}).get("name", "")))
                    for a in record.get("affected") or []
                    if a.get("package", {}).get("ecosystem") == ecosystem and a.get("package", {}).get("name")
                }
                if not vuln_id or not names:
                    continue
                db.execute(
                    "INSERT OR REPLACE INTO vulns VALUES (?, ?, ?)",
                    (vuln_id, record.get("modified"), json.dumps(record, separators=(",", ":"))),
                )
                db.executemany(
                    "INSERT INTO affected VALUES (?, ?, ?)", [(ecosystem, n, vuln_id) for n in names]
                )
                count += 1
        db.execute(
            "INSERT INTO meta VALUES ('built_at', ?)",
            (str(built_at if built_at is not None else time.time()),),
        )
        db.execute("INSERT INTO meta VALUES ('ecosystems', ?)", (",".join(sorted(archives)),))
    tmp.replace(target)
    return count


def built_at(path: Path) -> float | None:
    """When the mirror at `path` was built (epoch seconds); `None` if there is none."""
    if not path.exists():
        return None
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as db:
            row = db.execute("SELECT value FROM meta WHERE key = 'built_at'").fetchone()
    except sqlite3.Error:
        return None
    return float(row[0]) if row else None


# ───────────────────────── versions ─────────────────────────


def _semver_key(version: str) -> tuple[Any, ...] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?", version.strip())
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    # A release sorts after its pre-releases; pre-release identifiers compare numerically when numeric.
    pre_key: tuple[Any, ...] = (1,)
    if pre:
        pre_key = (0, *((0, int(p), "") if p.isdigit() else (1, 0, p) for p in pre.split(".")))
    return (int(major), int(minor), int(patch), pre_key)


def _pep440_key(version: str) -> Any | None:
    try:
        from packaging.version import InvalidVersion, Version
    except ImportError:  # pragma: no cover - packaging ships with both environments
        return _semver_key(version)
    try:
        return Version(version)
    except InvalidVersion:
        return None


def _key(ecosystem: str, version: str) -> Any | None:
    return _pep440_key(version) if ecosystem == "PyPI" else _semver_key(version)


def _in_range(ecosystem: str, version: Any, events: list[dict[str, str]]) -> bool:
    """OSV's `ECOSYSTEM`/`SEMVER` range: intervals opened by `introduced`, closed by `fixed`
    (excluded) or `last_affected` (included); an interval left open runs to the end."""
    start: Any | None = None
    opened = False
    for event in events:
        if "introduced" in event:
            value = event["introduced"]
            start = None if value == "0" else _key(ecosystem, value)
            opened = value == "0" or start is not None
            continue
        if not opened:
            continue
        above = start is None or version >= start
        if "fixed" in event and (end := _key(ecosystem, event["fixed"])) is not None:
            if above and version < end:
                return True
            opened = False
        elif "last_affected" in event and (end := _key(ecosystem, event["last_affected"])) is not None:
            if above and version <= end:
                return True
            opened = False
    return opened and (start is None or version >= start)


def affects(record: dict[str, Any], ecosystem: str, name: str, version: str | None) -> bool:
    """Whether `record` affects this package at this version (no version: any version)."""
    wanted = normalize(ecosystem, name)
    for affected in record.get("affected") or []:
        package = affected.get("package") or {}
        if (
            package.get("ecosystem") != ecosystem
            or normalize(ecosystem, str(package.get("name", ""))) != wanted
        ):
            continue
        if version is None:
            return True
        if version in (affected.get("versions") or []):
            return True
        key = _key(ecosystem, version)
        if key is None:
            continue
        for rng in affected.get("ranges") or []:
            if rng.get("type") in ("ECOSYSTEM", "SEMVER") and _in_range(
                ecosystem, key, rng.get("events") or []
            ):
                return True
    return False


# ───────────────────────── answering ─────────────────────────


class Mirror:
    """The two calls of OSV's API, answered from a mirror file, read-only."""

    def __init__(self, path: Path) -> None:
        # Read-only, and shared by the scanner's analyzer threads under one lock.
        self.db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        self.lock = threading.Lock()

    def close(self) -> None:
        self.db.close()

    def _matching(self, query: dict[str, Any]) -> list[dict[str, str]]:
        package = query.get("package") or {}
        ecosystem, name = str(package.get("ecosystem", "")), str(package.get("name", ""))
        if ecosystem not in ECOSYSTEMS or not name:
            return []
        version = query.get("version") or None
        with self.lock:
            rows = self.db.execute(
                "SELECT v.id, v.modified, v.record FROM affected a JOIN vulns v ON v.id = a.vuln_id "
                "WHERE a.ecosystem = ? AND a.name = ? ORDER BY v.id",
                (ecosystem, normalize(ecosystem, name)),
            ).fetchall()
        return [
            {"id": vuln_id, "modified": modified or ""}
            for vuln_id, modified, record in rows
            if affects(json.loads(record), ecosystem, name, version)
        ]

    def query_batch(self, queries: Iterable[dict[str, Any]]) -> dict[str, Any]:
        """`POST /v1/querybatch`: one result per query, each with the ids that affect it."""
        results = []
        for query in queries:
            vulns = self._matching(query)
            results.append({"vulns": vulns} if vulns else {})
        return {"results": results}

    def vuln(self, vuln_id: str) -> dict[str, Any] | None:
        """`GET /v1/vulns/{id}`: the whole record, or `None` (a 404)."""
        with self.lock:
            row = self.db.execute("SELECT record FROM vulns WHERE id = ?", (vuln_id,)).fetchone()
        return json.loads(row[0]) if row else None
