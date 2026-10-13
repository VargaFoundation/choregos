"""`validate_nginx_config` (S33-08, ADR 0052): `nginx -t` on the workspace's configuration, confined
to the workspace, and an absent nginx said as such — never taken for a valid configuration.

The nginx binary is a script here that behaves as `nginx -t` does on its exit code and stderr; a
real nginx runs in the runner image.
"""

from __future__ import annotations

import shutil
import stat
from pathlib import Path
from typing import Any

import pytest
from choregos_tools_mcp import McpServer, ToolContext


def _server(tmp_path: Path, binary: str) -> McpServer:
    return McpServer(client=None, context=ToolContext(workspace=tmp_path / "workspace", nginx_binary=binary))  # type: ignore[arg-type]


def _nginx(tmp_path: Path) -> str:
    """Accepts a file without `broken`; else answers like nginx on a syntax error. Logs its arguments."""
    binary = tmp_path / "nginx"
    binary.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$@" > {tmp_path}/arguments\n'
        'while [ "$1" != "-c" ]; do shift; done; config="$2"\n'
        'if grep -q broken "$config"; then\n'
        '  echo "nginx: [emerg] unknown directive \\"broken\\" in $config:2" >&2\n'
        '  echo "nginx: configuration file $config test failed" >&2; exit 1\n'
        "fi\n"
        'echo "nginx: configuration file $config test is successful" >&2\n',
        encoding="utf-8",
    )
    binary.chmod(binary.stat().st_mode | stat.S_IXUSR)
    return str(binary)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "workspace" / "nginx"
    root.mkdir(parents=True)
    (root / "nginx.conf").write_text("events {}\n", encoding="utf-8")
    (root / "bad.conf").write_text("events {}\nbroken on;\n", encoding="utf-8")
    return root


async def _call(server: McpServer, path: str) -> dict[str, Any]:
    return await server.call("validate_nginx_config", {"path": path})


async def test_a_valid_configuration_is_accepted_with_the_main_file_as_given(
    tmp_path: Path, workspace: Path
) -> None:
    result = await _call(_server(tmp_path, _nginx(tmp_path)), "nginx/nginx.conf")
    assert not result["isError"] and result["content"][0]["text"].startswith("ok:")
    arguments = (tmp_path / "arguments").read_text(encoding="utf-8").split("\n")
    assert arguments[:3] == ["-t", "-e", "stderr"]
    assert arguments[arguments.index("-c") + 1] == str(workspace / "nginx.conf")
    assert not str(arguments[arguments.index("-p") + 1]).startswith(str(tmp_path / "workspace")), (
        "logs go to a throwaway prefix, not into the repository"
    )


async def test_an_invalid_configuration_is_refused_with_nginx_s_own_words(
    tmp_path: Path, workspace: Path
) -> None:
    result = await _call(_server(tmp_path, _nginx(tmp_path)), "nginx/bad.conf")
    assert result["isError"]
    text = result["content"][0]["text"]
    assert 'unknown directive "broken" in nginx/bad.conf:2' in text, "paths relative to the workspace"


@pytest.mark.parametrize("path", ["../outside.conf", "/etc/nginx/nginx.conf", "nginx/missing.conf"])
async def test_a_path_out_of_the_workspace_or_absent_is_refused(
    tmp_path: Path, workspace: Path, path: str
) -> None:
    (tmp_path / "outside.conf").write_text("events {}\n", encoding="utf-8")
    result = await _call(_server(tmp_path, _nginx(tmp_path)), path)
    assert result["isError"]
    assert not (tmp_path / "arguments").exists(), "nginx was not even called"


async def test_without_nginx_the_configuration_is_said_not_validated(tmp_path: Path, workspace: Path) -> None:
    result = await _call(_server(tmp_path, str(tmp_path / "absent-nginx")), "nginx/nginx.conf")
    assert result["isError"] and "NOT validated" in result["content"][0]["text"]


@pytest.mark.skipif(shutil.which("nginx") is None, reason="nginx is in the runner image, not on this machine")
async def test_the_real_nginx_accepts_a_minimal_configuration(tmp_path: Path, workspace: Path) -> None:
    result = await _call(_server(tmp_path, "nginx"), "nginx/nginx.conf")
    assert not result["isError"], result


def test_the_pid_goes_to_the_throwaway_prefix_unless_the_configuration_names_one() -> None:
    """`nginx -t` opens its pid file; the distribution's is root's — the CI caught it (PR #389)."""
    from choregos_tools_mcp.nginx import command

    plain = command("nginx", "/tmp/x", Path("/w/nginx.conf"), "events {}\n")
    assert plain[-2:] == ["-g", "pid /tmp/x/nginx.pid;"]
    own = command("nginx", "/tmp/x", Path("/w/nginx.conf"), "pid /run/own.pid;\nevents {}\n")
    assert "-g" not in own, "a second `pid` would be a duplicate directive"
