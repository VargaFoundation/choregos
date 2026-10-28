"""`plugins/offline/` and its `plugins.lock` (S43-09): the archives are what the lock says.

`make plugins-check` runs the same check; a refresh (`make plugins-lock`) needs the network.
"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path

from tools.plugins.update_lock import problems, read

ROOT = Path(__file__).resolve().parents[2]
OFFLINE = ROOT / "plugins" / "offline"


def test_the_archives_are_what_the_lock_says() -> None:
    assert problems(OFFLINE) == []
    _, locked = read(OFFLINE)
    assert set(locked) == {"software-delivery", "netops", "authoring", "mcp-clients"}
    for entry in locked.values():
        assert entry.source == f"ghcr.io/vargafoundation/choregos-agents/{entry.name}:{entry.version}"


def test_a_changed_archive_or_an_unlisted_one_is_caught(tmp_path: Path) -> None:
    shutil.copytree(OFFLINE, tmp_path / "offline")
    copy = tmp_path / "offline"
    shutil.copy(copy / "netops-1.0.0.tar.gz", copy / "extra-1.0.0.tar.gz")
    with tarfile.open(OFFLINE / "authoring-1.0.0.tar.gz") as source:
        source.extractall(tmp_path / "authoring", filter="data")
    agent = tmp_path / "authoring" / "agents" / "agent-drafter.yaml"
    agent.write_text(agent.read_text(encoding="utf-8") + "\n# one more line\n", encoding="utf-8")
    with tarfile.open(copy / "authoring-1.0.0.tar.gz", "w:gz") as target:
        for path in sorted((tmp_path / "authoring").rglob("*")):
            target.add(path, path.relative_to(tmp_path / "authoring").as_posix(), recursive=False)
    found = problems(copy)
    assert any("extra-1.0.0.tar.gz: an archive the lock does not name" in p for p in found), found
    assert any(p.startswith("authoring 1.0.0 is sha256:") for p in found), found
