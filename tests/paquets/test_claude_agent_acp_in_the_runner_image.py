"""The runner image carries the maintained Claude Code ACP adapter (#436).

`@zed-industries/claude-code-acp` is deprecated: its last version, 0.16.2, was the image's. The
image installs `@agentclientprotocol/claude-agent-acp` at the `versions.lock` pin, calls it at
build time, and keeps `claude-code-acp` as a link to it, so a stage input that still names the old
binary (the contract's example does) starts the new adapter.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]
DOCKERFILE = ROOT / "docker/runner.Dockerfile"
LOCK = ROOT / "packages/runner/src/choregos_runner/backends/versions.lock"


def _runtime() -> str:
    text = DOCKERFILE.read_text(encoding="utf-8")
    return text[text.index("AS runtime") :]


def test_the_image_installs_the_maintained_adapter_at_its_pin() -> None:
    runtime = _runtime()
    assert '"@agentclientprotocol/claude-agent-acp@$(version claude-agent-acp)"' in runtime
    assert "@zed-industries/claude-code-acp@" not in runtime
    pin = re.search(r"^claude-agent-acp=(\d+)\.(\d+)\.(\d+)$", LOCK.read_text(encoding="utf-8"), re.M)
    assert pin, "versions.lock pins no exact claude-agent-acp version"
    # The new package's line starts long after the Zed package's last version (0.16.2).
    assert (int(pin[1]), int(pin[2])) > (0, 16)


def test_the_build_calls_the_adapter_and_its_old_name() -> None:
    runtime = _runtime()
    assert 'ln -s "$(command -v claude-agent-acp)" /usr/local/bin/claude-code-acp' in runtime
    assert "timeout 30 claude-agent-acp --version >/dev/null </dev/null" in runtime
    assert "timeout 30 claude-code-acp --version >/dev/null </dev/null" in runtime
    # The link is made where the adapter is installed, before the check that calls it.
    assert runtime.index('ln -s "$(command -v claude-agent-acp)"') < runtime.index(
        "timeout 30 claude-code-acp --version"
    )
