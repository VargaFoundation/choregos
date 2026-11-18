# SPDX-License-Identifier: Apache-2.0
"""A second scanner behind the skill gate: SkillSpector, offline, on a local OSV mirror (ADR 0095).

The gate's own scan (`skill_gate`, ADR 0050) reads patterns. SkillSpector adds what patterns miss:
the AST of a script, taint from a secret to a socket, YARA rules, MCP tool poisoning, and known
vulnerable dependencies. It runs in a child process with its own interpreter (its dependencies are
not the platform's), `--no-llm`, with every HTTP client answered by the local OSV mirror and nothing
else — no call leaves the pod at scan time (the owner's rule of 2026-10-26).

Its findings join the gate's as `skillspector:<rule>`: HIGH and CRITICAL count as critical (the
version waits for a second administrator), MEDIUM as a warning, LOW as information. A scanner that
fails is a critical finding, never a pass. A scan without a fresh mirror is incomplete, and says so.
"""

from __future__ import annotations

import asyncio
import json
import stat
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
import structlog

from ..config import Settings, get_settings
from ..knowledge.conversion import child_environment
from ..scanners import osv_mirror
from .skill_gate import RANK, Assessment, Finding, Severity

logger = structlog.get_logger(__name__)

SHIM = Path(__file__).resolve().parent.parent / "scanners" / "skillspector_offline.py"
PREFIX = "skillspector:"
_SEVERITIES: dict[str, Severity] = {
    "CRITICAL": "critical",
    "HIGH": "critical",
    "MEDIUM": "warning",
    "LOW": "info",
    "INFO": "info",
}
#: What a finding's detail may hold of the scanner's text.
DETAIL_CHARS = 300


@dataclass(frozen=True)
class Scan:
    """What a scanner says about one version."""

    findings: list[Finding] = field(default_factory=list)
    #: The scanner's own word: SAFE, CAUTION, DO_NOT_INSTALL; `None` when it gave none.
    recommendation: str | None = None


def with_scan(assessment: Assessment, scan: Scan) -> Assessment:
    """The gate's assessment, with the scanner's findings: a critical one, or the scanner's own
    DO_NOT_INSTALL, makes the version high risk; its CAUTION makes it at least medium."""
    risk = assessment.risk
    if scan.recommendation == "DO_NOT_INSTALL" or any(f.severity == "critical" for f in scan.findings):
        risk = "high"
    elif scan.recommendation == "CAUTION" and RANK[risk] < RANK["medium"]:
        risk = "medium"
    return Assessment(risk=risk, findings=[*assessment.findings, *scan.findings], licence=assessment.licence)


class SkillScanner(Protocol):
    async def scan(self, files: dict[str, str], executables: list[str]) -> Scan: ...


def findings_from_report(report: dict[str, Any]) -> Scan:
    """SkillSpector's JSON report, as the gate's findings."""
    findings: list[Finding] = []
    for issue in report.get("issues") or []:
        location = issue.get("location") or {}
        findings.append(
            Finding(
                f"{PREFIX}{issue.get('id') or 'unknown'}",
                _SEVERITIES.get(str(issue.get("severity", "")).upper(), "warning"),
                str(issue.get("pattern") or issue.get("explanation") or issue.get("category") or "")[
                    :DETAIL_CHARS
                ],
                location.get("file"),
                location.get("start_line"),
            )
        )
    completeness = report.get("analysis_completeness") or {}
    if completeness and not completeness.get("is_complete", False):
        reasons = sorted(
            {
                f"{e.get('analyzers', ['?'])[0] if e.get('analyzers') else '?'}: {e.get('reason_code')}"
                for e in completeness.get("ledger_exceptions") or []
            }
        )
        findings.append(
            Finding(
                "scanner_incomplete",
                "warning",
                f"SkillSpector read {completeness.get('coverage_percent', 0)} % of the version"
                + (f" ({'; '.join(reasons)[:200]})" if reasons else ""),
            )
        )
    recommendation = (report.get("risk_assessment") or {}).get("recommendation")
    return Scan(findings=findings, recommendation=str(recommendation) if recommendation else None)


def _failed(detail: str) -> Scan:
    return Scan(
        findings=[
            Finding("scanner_failed", "critical", f"SkillSpector did not finish: {detail}"[:DETAIL_CHARS])
        ]
    )


def _write(root: Path, files: dict[str, str], executables: list[str]) -> None:
    for path, text in files.items():
        target = (root / path).resolve()
        if root.resolve() not in target.parents:
            continue  # the gate validated the paths already; nothing escapes the scan directory
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        if path in executables:
            target.chmod(target.stat().st_mode | stat.S_IXUSR)


class SkillSpectorScanner:
    """SkillSpector in its own interpreter, offline (`scanners/skillspector_offline.py`)."""

    def __init__(self, python: str, mirror: Path, *, timeout_s: float, max_age_s: float) -> None:
        self.python = python
        self.mirror = mirror
        self.timeout_s = timeout_s
        self.max_age_s = max_age_s

    def _fresh_mirror(self) -> Path | None:
        built = osv_mirror.built_at(self.mirror)
        return self.mirror if built is not None and time.time() - built <= self.max_age_s else None

    async def scan(self, files: dict[str, str], executables: list[str]) -> Scan:
        if not await asyncio.to_thread(Path(self.python).exists):
            return _failed(f"no interpreter at {self.python}")
        mirror = self._fresh_mirror()
        with tempfile.TemporaryDirectory(prefix="choregos-skill-scan-") as tmp:
            root = Path(tmp) / "skill"
            await asyncio.to_thread(_write, root, files, executables)
            report = Path(tmp) / "report.json"
            env = {**child_environment(), "HOME": tmp}
            process = await asyncio.create_subprocess_exec(
                self.python,
                "-I",
                str(SHIM),
                str(root),
                str(report),
                str(mirror or ""),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=tmp,
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=self.timeout_s)
            except TimeoutError:
                process.kill()
                await process.wait()
                return _failed(f"stopped after {self.timeout_s:g} s")
            if not report.exists():
                return _failed(
                    stderr.decode("utf-8", "replace").strip()[-200:] or f"exit {process.returncode}"
                )
            try:
                scan = findings_from_report(json.loads(report.read_text(encoding="utf-8")))
            except (OSError, ValueError) as exc:
                return _failed(f"unreadable report: {exc}")
        if mirror is None:
            scan.findings.append(
                Finding(
                    "osv_mirror_missing",
                    "warning",
                    "no fresh local OSV mirror: known vulnerable dependencies were not checked",
                )
            )
        return scan


@dataclass
class FakeSkillScanner:
    """Scriptable (ADR 0005): the scan it returns, and the versions it was given."""

    answer: Scan = field(default_factory=Scan)
    seen: list[dict[str, str]] = field(default_factory=list)

    async def scan(self, files: dict[str, str], executables: list[str]) -> Scan:
        self.seen.append(dict(files))
        return self.answer


#: For tests: the scanner the gate uses, whatever the settings say (`set_scanner`).
_override: list[SkillScanner] = []


def set_scanner(scanner: SkillScanner | None) -> None:
    """For tests: the scanner the gate uses, whatever the settings say; `None` restores them."""
    _override[:] = [scanner] if scanner is not None else []


def get_scanner(settings: Settings | None = None) -> SkillScanner | None:
    """The scanner the deployment chose; `None` when it chose none."""
    if _override:
        return _override[0]
    settings = settings or get_settings()
    if settings.skill_scanner != "skillspector":
        return None
    return SkillSpectorScanner(
        settings.skillspector_python,
        Path(settings.osv_mirror_path),
        timeout_s=settings.skill_scan_timeout_s,
        max_age_s=settings.osv_max_age_hours * 3600,
    )


# ───────────────────────── the mirror ─────────────────────────


async def _download(client: httpx.AsyncClient, url: str, target: Path) -> None:
    async with client.stream("GET", url) as response:
        response.raise_for_status()
        with target.open("wb") as handle:
            async for chunk in response.aiter_bytes(1 << 20):
                handle.write(chunk)


async def refresh_mirror(settings: Settings, *, client: httpx.AsyncClient | None = None) -> int:
    """Downloads OSV's PyPI and npm archives and rebuilds the mirror; returns its record count."""
    target = Path(settings.osv_mirror_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    base = settings.osv_mirror_url.rstrip("/")
    owned = client is None
    client = client or httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True)
    try:
        with tempfile.TemporaryDirectory(prefix="osv-", dir=target.parent) as tmp:
            archives = {}
            for ecosystem in osv_mirror.ECOSYSTEMS:
                archive = Path(tmp) / f"{ecosystem}.zip"
                await _download(client, f"{base}/{ecosystem}/all.zip", archive)
                archives[ecosystem] = archive
            return await asyncio.to_thread(osv_mirror.build, archives, target)
    finally:
        if owned:
            await client.aclose()


async def mirror_loop(settings: Settings | None = None) -> None:
    """Keeps the mirror younger than `osv_refresh_hours`, in this replica, for its whole life."""
    settings = settings or get_settings()
    if settings.skill_scanner != "skillspector" or not settings.osv_mirror_url:
        return
    period = settings.osv_refresh_hours * 3600
    while True:
        built = osv_mirror.built_at(Path(settings.osv_mirror_path))
        due = 0.0 if built is None else max(0.0, built + period - time.time())
        await asyncio.sleep(due)
        try:
            count = await refresh_mirror(settings)
            logger.info("osv mirror refreshed", records=count, path=settings.osv_mirror_path)
        except Exception as exc:  # the next attempt comes; the scan says the mirror is stale meanwhile
            logger.warning("osv mirror refresh failed", error=str(exc)[:300])
            await asyncio.sleep(min(period, 3600))


__all__ = [
    "PREFIX",
    "FakeSkillScanner",
    "Scan",
    "SkillScanner",
    "SkillSpectorScanner",
    "findings_from_report",
    "get_scanner",
    "mirror_loop",
    "refresh_mirror",
    "set_scanner",
    "with_scan",
]
