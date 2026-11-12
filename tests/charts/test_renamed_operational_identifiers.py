"""The operational names renamed in 0.21 (S45-33, ADR 0098): alerts, rule group, dashboards, runbooks.

Four things outside the code can break silently when a name moves: an Alertmanager route keyed on an
alert name, a Grafana link keyed on a dashboard uid, a deployed alert's `runbook_url`, and an
upgrade note that forgets one of them. The rendered rule, the dashboards and the page are read here,
not a copy of them. The `legacy_alertname` label and the runbook redirect stubs kept the former
names for one window; both are gone since 0.24.0 (S45-49). The Grafana uids stay frozen.
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import subprocess
from typing import Any

import pytest
import yaml
from choregos_core.publishing import PUBLISHING

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHART = ROOT / "charts" / "choregos"
RUNBOOKS = ROOT / "docs" / "runbooks"
RUNBOOK_URL = re.compile(rf"^{re.escape(PUBLISHING.source_file(''))}(docs/runbooks/[\w.-]+\.md)$")

#: Former alert name -> new alert name. `ChoregosBacklogTemporal` was already English and stays.
RENAMED_ALERTS = {
    "ChoregosApiErreurs5xx": "ChoregosApi5xxErrors",
    "ChoregosRunEnAttente": "ChoregosRunPending",
    "ChoregosBudgetJournalier": "ChoregosDailyBudgetHigh",
    "ChoregosTrainGele": "ChoregosTrainFrozen",
}
#: The Grafana uids are frozen: a link, a folder or an alert annotation points at them.
FROZEN_UIDS = {
    "costs.json": "choregos-couts",
    "memory.json": "choregos-memoire",
    "platform.json": "choregos-plateforme",
}
RENAMED_RUNBOOKS = {
    "montee-temporal.md": "temporal-upgrade.md",
    "perte-de-noeud.md": "node-loss.md",
    "restauration-postgres.md": "postgres-restore.md",
    "run-bloque.md": "stuck-run.md",
    "train-gele.md": "train-frozen.md",
    "webhook-perdu.md": "lost-webhook.md",
    "rotation-secrets.md": "secret-rotation.md",
}
#: Every former identifier the "Renamed in 0.21" tables must name (alert, rule group, Kyverno rule
#: and policy, template, init container, mount path, dashboard ConfigMap).
FORMER_NAMES = [
    *RENAMED_ALERTS,
    "choregos.plateforme",
    "interdire-latest",
    "verifier-signature",
    "pas-de-hostpath",
    "labels-obligatoires",
    "choregos-quotas-et-runtime",
    "templates/garde.yaml",
    "templates/secrets-embarques.yaml",
    "attendre-la-base",
    "/etc/choregos/outils",
    "choregos-dashboard-couts",
    "choregos-dashboard-memoire",
    "choregos-dashboard-plateforme",
    *(f"docs/runbooks/{old}" for old in RENAMED_RUNBOOKS),
]
HELM = shutil.which("helm") is not None


def _rules_from_the_template() -> list[dict[str, Any]]:
    """The `choregos.alertRules` block of `_helpers.tpl`, which is plain YAML between its markers."""
    text = (CHART / "templates" / "_helpers.tpl").read_text("utf-8")
    block = text.split('{{- define "choregos.alertRules" -}}\n', 1)[1].split("{{- end }}", 1)[0]
    rules: list[dict[str, Any]] = yaml.safe_load(block)
    return rules


def _rendered_rule(flavour: str) -> dict[str, Any]:
    command = ["helm", "template", "choregos", str(CHART), "--set", f"monitoring.flavour={flavour}"]
    command += ["--set", "monitoring.alerts=true"]
    rendered = subprocess.run(command, capture_output=True, text=True, check=True)
    kind = "VMRule" if flavour == "victoriametrics" else "PrometheusRule"
    (rule,) = [d for d in yaml.safe_load_all(rendered.stdout) if d and d.get("kind") == kind]
    assert isinstance(rule, dict)
    return rule


def test_alerts_have_english_names_and_no_legacy_label() -> None:
    rules = _rules_from_the_template()
    by_name = {r["alert"]: r for r in rules}
    for former, new in RENAMED_ALERTS.items():
        assert new in by_name, f"{new} is missing: the alerts are {sorted(by_name)}"
        assert former not in by_name, f"{former} must not be an alert name any more"
    assert "ChoregosBacklogTemporal" in by_name
    for rule in rules:
        assert "legacy_alertname" not in rule["labels"], f"{rule['alert']}: the label was removed in 0.24.0"


@pytest.mark.skipif(not HELM, reason="helm absent")
@pytest.mark.parametrize("flavour", ["prometheus", "victoriametrics"])
def test_the_rendered_rule_is_in_the_english_group(flavour: str) -> None:
    rule = _rendered_rule(flavour)
    (group,) = rule["spec"]["groups"]
    assert group["name"] == "choregos.platform"
    alerts = {r["alert"] for r in group["rules"]}
    assert set(RENAMED_ALERTS.values()) <= alerts
    assert not [r["alert"] for r in group["rules"] if "legacy_alertname" in r["labels"]]


def test_runbook_urls_resolve() -> None:
    urls = [
        r["annotations"]["runbook_url"]
        for r in _rules_from_the_template()
        if "runbook_url" in r["annotations"]
    ]
    assert urls, "the chart ships runbook URLs"
    for url in urls:
        match = RUNBOOK_URL.match(url)
        assert match, f"{url} is not an absolute runbook URL"
        path = ROOT / match.group(1)
        assert path.is_file(), f"{url}: {path} does not exist"
        assert "Moved" not in path.read_text("utf-8").splitlines()[0], f"{url} points at a redirect stub"


def test_every_former_runbook_stub_is_gone() -> None:
    for old, new in RENAMED_RUNBOOKS.items():
        assert not (RUNBOOKS / old).exists(), f"{old}: the redirect stub was removed in 0.24.0"
        assert (RUNBOOKS / new).is_file(), new


def test_dashboard_uids_are_frozen() -> None:
    present = {p.name for p in (CHART / "dashboards").glob("*.json")}
    assert set(FROZEN_UIDS) <= present, f"missing dashboards: {set(FROZEN_UIDS) - present}"
    for name, uid in FROZEN_UIDS.items():
        assert json.loads((CHART / "dashboards" / name).read_text("utf-8"))["uid"] == uid


def test_upgrade_notes_list_every_renamed_identifier() -> None:
    text = (ROOT / "docs" / "deployment.md").read_text("utf-8")
    assert "## Renamed in 0.21 (ADR 0098)" in text
    section = text.split("## Renamed in 0.21 (ADR 0098)", 1)[1]
    rows = {line for line in section.splitlines() if line.startswith("|")}
    for name in FORMER_NAMES:
        assert any(f"`{name}" in row for row in rows), f"the 'Renamed in 0.21' tables do not name {name}"
    for new in RENAMED_ALERTS.values():
        assert any(f"`{new}`" in row for row in rows), f"the tables do not name the new alert {new}"
    assert "legacy_alertname" in section
    upgrade = text.split("## Upgrading to 0.24", 1)[1].split("\n## ", 1)[0]
    assert "`legacy_alertname`" in upgrade, "the 0.24 notes must say the label is gone"
    for old in RENAMED_RUNBOOKS:
        assert f"docs/runbooks/{old}" in upgrade, f"the 0.24 notes do not name the removed stub {old}"
    assert "choregos-plugins" in section, "the pod-security note must warn about the plugin namespace"
