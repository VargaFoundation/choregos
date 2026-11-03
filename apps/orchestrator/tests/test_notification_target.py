# SPDX-License-Identifier: Apache-2.0
"""Every notification site addresses the project's e-mail addresses when the notifier is e-mail."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import choregos_orchestrator
from choregos_orchestrator.activities.base import notification_target


def _bundle(kind: str, emails: list[str], slack_channel: str = "") -> SimpleNamespace:
    notifier = SimpleNamespace(channel_kind=kind) if kind else SimpleNamespace()
    return SimpleNamespace(
        adapters=SimpleNamespace(notify=notifier),
        config=SimpleNamespace(notify=SimpleNamespace(emails=emails, slack_channel=slack_channel)),
    )


def test_an_email_notifier_gets_the_project_addresses() -> None:
    assert notification_target(_bundle("email", ["a@x.dev", "b@y.dev"])) == "a@x.dev,b@y.dev"
    assert notification_target(_bundle("email", [], "#ops")) == "#ops"
    assert notification_target(_bundle("", ["a@x.dev"], "#ops")) == "#ops"
    assert notification_target(_bundle("", [])) == "#choregos"


def test_no_activity_reads_the_slack_channel_directly() -> None:
    root = Path(choregos_orchestrator.__file__).parent
    base = root / "activities" / "base.py"
    offenders = [
        str(path.relative_to(root))
        for path in root.rglob("*.py")
        if path != base and "slack_channel or" in path.read_text(encoding="utf-8")
    ]
    assert offenders == []
