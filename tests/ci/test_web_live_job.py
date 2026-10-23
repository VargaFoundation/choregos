"""The CI job where the console's live journeys run against the real API (S40-17d).

S40-17c wrote the journeys (`apps/web/e2e-live/`) and left them opt-in: no job started the API beside
the console, so a console that sends a field the API ignores (a 200, then nothing stored) stayed
green. These checks keep the job running every live journey, behind the gate, on the paths a
journey crosses.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
CI = ROOT / ".github" / "workflows" / "ci.yml"


def _workflow() -> dict[str, Any]:
    return dict(yaml.safe_load(CI.read_text(encoding="utf-8")))


def _job() -> dict[str, Any]:
    return dict(_workflow()["jobs"]["web-live"])


def test_the_job_runs_the_live_journeys_with_both_servers() -> None:
    steps = _job()["steps"]
    runs = [str(step.get("run", "")) for step in steps]
    assert "uv sync --frozen" in runs, "the API is started from the workspace by `tools/e2e_live_api.py`"
    assert "pnpm e2e:live" in runs
    assert not any("if" in step for step in steps if step.get("run")), "no step of the proof is skipped"
    config = (ROOT / "apps" / "web" / "playwright.live.config.ts").read_text(encoding="utf-8")
    assert 'testDir: "./e2e-live"' in config
    assert "tools/e2e_live_api.py" in config
    journeys = sorted(p.name for p in (ROOT / "apps" / "web" / "e2e-live").glob("*.live.spec.ts"))
    assert {"operation-fields.live.spec.ts", "operation-result-cap.live.spec.ts"} <= set(journeys), journeys


def test_the_gate_waits_for_the_job_and_the_filter_covers_both_sides() -> None:
    workflow = _workflow()
    assert "web-live" in workflow["jobs"]["ci-ok"]["needs"]
    assert "web-live" in workflow["jobs"]["changes"]["outputs"]
    filters = yaml.safe_load(workflow["jobs"]["changes"]["steps"][1]["with"]["filters"])["web-live"]
    for path in ("apps/web/**", "apps/api/**", "packages/contracts/**", "tools/e2e_live_api.py"):
        assert path in filters, path
    assert "github.ref == 'refs/heads/main'" in _job()["if"]
