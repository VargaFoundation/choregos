# SPDX-License-Identifier: Apache-2.0
"""`choregos artifacts` (S67-11, ADR 0154 §2): list, download, open, compare.

The API is faked with respx; every payload the fake serves is validated against `openapi.yaml` (`contract`).
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

import pytest
import respx
from httpx import Response
from typer.testing import CliRunner

SCHEMAS = Path(__file__).resolve().parents[3] / "packages" / "contracts" / "schemas"
API = "http://api.test"
V1 = f"{API}/api/v1"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
ITEM = "wi-12"
#: Bytes a text decoder would damage: a lone 0xff, CRLF, a trailing NUL. Written back unchanged or not at all.
BINARY = b"\x89PNG\r\n\x1a\n\xff\xfe\x00data\r\n\x00"


@pytest.fixture(autouse=True)
def profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from tranibor_cli import client as client_module
    from tranibor_cli import theme

    monkeypatch.setattr(client_module, "CONFIG_PATH", tmp_path / "config.json")
    monkeypatch.setattr(theme, "MONOCHROME", False)
    monkeypatch.setenv("TRANIBOR_API_URL", API)
    monkeypatch.setenv("TRANIBOR_TOKEN", "chg_test")
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.delenv("NO_COLOR", raising=False)
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setenv("TMPDIR", str(tmp_path))


def _digest(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _artifact(name: str, version: int, data: bytes, type_: str = "markdown") -> dict[str, Any]:
    return {
        "name": name,
        "type": type_,
        "kind": "markdown" if type_ == "markdown" else "file",
        "media_type": "text/markdown" if type_ == "markdown" else "image/png",
        "version": version,
        "digest": _digest(data),
        "size": len(data),
        "produced_by": {"run_id": "run-a1", "stage": "implement", "attempt": 1, "agent": "coder"},
    }


def _valid_artifact(row: dict[str, Any]) -> dict[str, Any]:
    """`Artifact` in `openapi.yaml` is a `$ref` to this schema file, which the shared `contract` fixture
    does not follow: validate against the file itself, the same contract."""
    import json

    import jsonschema

    schema = json.loads((SCHEMAS / "artifact.schema.json").read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(row)
    return row


class Platform:
    def __init__(self, router: respx.Router, contract: Any) -> None:
        self.router, self.contract = router, contract

    def listing(self, *rows: dict[str, Any]) -> None:
        body = {
            "items": [_valid_artifact(row) for row in rows],
            "meta": {"has_more": False, "next_cursor": None},
        }
        self.router.get(f"{V1}/work-items/{ITEM}/artifacts").mock(return_value=Response(200, json=body))

    def content(
        self,
        ref: str,
        data: bytes,
        media_type: str = "text/markdown",
        digest: str | None = "",
        status: int = 200,
    ) -> respx.Route:
        headers = {"content-type": media_type, "X-Choregos-Digest": digest or _digest(data)}
        if digest is None:
            del headers["X-Choregos-Digest"]
        response = (
            Response(200, content=data, headers=headers)
            if status == 200
            else Response(status, json={"title": "Not found", "detail": "no such revision"})
        )
        return self.router.get(f"{V1}/work-items/{ITEM}/artifacts/{ref}/content").mock(return_value=response)


@pytest.fixture
def platform(contract: Any) -> Any:
    with respx.mock(assert_all_called=False) as router:
        yield Platform(router, contract)


def _run(*words: str, env: dict[str, str] | None = None) -> Any:
    from tranibor_cli.main import app

    return CliRunner().invoke(app, ["artifacts", *words], env=env)


def test_list_shows_every_revision_and_json_is_the_apis_list(platform: Platform) -> None:
    platform.listing(_artifact("spec.md", 1, b"a"), _artifact("spec.md", 2, b"ab"))
    result = _run("list", ITEM)
    assert result.exit_code == 0, result.output
    said = " ".join(re.sub(r"[│┃]", " ", ANSI.sub("", result.output)).split())
    assert "spec.md 1 markdown 1 run-a1" in said and "spec.md 2 markdown 2 run-a1" in said
    as_json = _run("list", ITEM, "--json")
    assert as_json.exit_code == 0 and as_json.output.count('"version"') == 2


def test_inbox_lists_what_the_tasks_delivered(platform: Platform) -> None:
    item = {
        "id": "in-1",
        "task": "weekly-report",
        "installation": "inst-1",
        "work_item_id": ITEM,
        "name": "report.md",
        "version": 3,
        "type": "markdown",
        "media_type": "text/markdown",
        "created_at": "2026-11-16T08:00:00Z",
    }
    body = platform.contract("InboxList", {"items": [item]})
    route = platform.router.get(f"{V1}/me/inbox").mock(return_value=Response(200, json=body))
    result = _run("inbox", "--limit", "5")
    assert result.exit_code == 0, result.output
    assert route.calls.last.request.url.params["limit"] == "5"
    assert "report.md" in result.output and "weekly-report" in result.output


def test_a_download_writes_exactly_the_bytes_the_api_served(platform: Platform, tmp_path: Path) -> None:
    route = platform.content("logo.png", BINARY, "image/png")
    target = tmp_path / "out.png"
    result = _run("download", ITEM, "logo.png", str(target))
    assert result.exit_code == 0, result.output
    assert target.read_bytes() == BINARY
    assert route.calls.last.request.url.params["download"] == "true"


def test_a_download_names_the_file_after_the_artefact_in_a_directory(
    platform: Platform, tmp_path: Path
) -> None:
    platform.content("spec.md@2", b"# v2\n")
    result = _run("download", ITEM, "spec.md@2", str(tmp_path))
    assert result.exit_code == 0, result.output
    assert (tmp_path / "spec.md").read_bytes() == b"# v2\n"


def test_an_existing_file_is_kept_without_force_and_replaced_with_it(
    platform: Platform, tmp_path: Path
) -> None:
    platform.content("spec.md", b"new\n")
    target = tmp_path / "spec.md"
    target.write_bytes(b"mine\n")
    refused = _run("download", ITEM, "spec.md", str(target))
    assert refused.exit_code == 1 and "--force" in refused.output
    assert target.read_bytes() == b"mine\n"
    forced = _run("download", ITEM, "spec.md", str(target), "--force")
    assert forced.exit_code == 0 and target.read_bytes() == b"new\n"


def test_bytes_that_do_not_match_the_announced_digest_are_not_written(
    platform: Platform, tmp_path: Path
) -> None:
    platform.content("spec.md", b"tampered", digest=_digest(b"original"))
    target = tmp_path / "spec.md"
    result = _run("download", ITEM, "spec.md", str(target))
    assert result.exit_code == 1 and "announced" in " ".join(result.output.split())
    assert not target.exists()


def test_a_missing_revision_exits_1_and_writes_nothing(platform: Platform, tmp_path: Path) -> None:
    platform.content("spec.md@9", b"", status=404)
    target = tmp_path / "spec.md"
    result = _run("download", ITEM, "spec.md@9", str(target))
    assert result.exit_code == 1 and "no such revision" in " ".join(result.output.split())
    assert not target.exists()


def test_open_hands_a_private_temporary_copy_to_the_injected_opener(
    platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tranibor_cli import artifacts

    platform.content("logo.png", BINARY, "image/png")
    seen: list[tuple[Path, bytes, int]] = []

    def opener(path: Path) -> None:
        seen.append((path, path.read_bytes(), path.stat().st_mode & 0o777))

    monkeypatch.setattr(artifacts, "system_opener", opener)
    result = _run("open", ITEM, "logo.png")
    assert result.exit_code == 0, result.output
    [(path, data, mode)] = seen
    assert path.name == "logo.png" and data == BINARY and mode == 0o600


def test_an_opener_that_cannot_start_is_a_red_line(
    platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tranibor_cli import artifacts

    platform.content("spec.md", b"x")

    def missing(path: Path) -> None:
        raise artifacts.ArtifactError("cannot start xdg-open: No such file or directory")

    monkeypatch.setattr(artifacts, "system_opener", missing)
    result = _run("open", ITEM, "spec.md")
    assert result.exit_code == 1 and "cannot start xdg-open" in " ".join(result.output.split())


def test_each_platform_uses_its_own_opener() -> None:
    from tranibor_cli.artifacts import viewer_command

    p = Path("/tmp/x/spec.md")
    assert viewer_command(p, "Darwin")[0] == "open"
    assert viewer_command(p, "Windows")[0] == "explorer"
    assert viewer_command(p, "Linux")[0] == "xdg-open"
    assert viewer_command(p, "Linux", wsl=True, which=lambda name: "/usr/bin/wslview") == ["wslview", str(p)]
    assert viewer_command(
        p, "Linux", wsl=True, which=lambda name: None, to_windows=lambda _: r"\\wsl$\x"
    ) == [
        "explorer.exe",
        r"\\wsl$\x",
    ]


def test_wsl_is_recognised_from_the_environment_or_the_kernel() -> None:
    from tranibor_cli.artifacts import is_wsl

    assert is_wsl({"WSL_DISTRO_NAME": "Ubuntu"}, "6.1.0")
    assert is_wsl({}, "6.18.33.2-microsoft-standard-WSL2")
    assert not is_wsl({}, "6.1.0-generic")


V1_TEXT = b"# Spec\n\none\ntwo\nthree\n"
V2_TEXT = b"# Spec\n\none\n2\nthree\nfour\n"


def test_compare_shows_a_unified_diff_of_two_versions_coloured(platform: Platform) -> None:
    platform.content("spec.md@1", V1_TEXT)
    platform.content("spec.md@2", V2_TEXT)
    result = _run("compare", ITEM, "spec.md@1", "spec.md@2", env={"FORCE_COLOR": "1"})
    assert result.exit_code == 0, result.output
    plain = ANSI.sub("", result.output).splitlines()
    assert plain[:2] == ["--- spec.md@1", "+++ spec.md@2"]
    assert "-two" in plain and "+2" in plain and "+four" in plain
    assert "\x1b[32m+2" in result.output and "\x1b[31m-two" in result.output


def test_compare_with_no_color_has_no_escape_sequence_and_keeps_the_marks(platform: Platform) -> None:
    platform.content("spec.md@1", V1_TEXT)
    platform.content("spec.md", V2_TEXT)
    result = _run("compare", ITEM, "spec.md@1", "spec.md", env={"NO_COLOR": "1", "FORCE_COLOR": "1"})
    assert result.exit_code == 0, result.output
    assert not ANSI.search(result.output)
    assert "-two" in result.output and "+four" in result.output


def test_compare_of_identical_revisions_says_so(platform: Platform) -> None:
    platform.content("spec.md@1", V1_TEXT)
    platform.content("spec.md@2", V1_TEXT)
    result = _run("compare", ITEM, "spec.md@1", "spec.md@2")
    assert result.exit_code == 0 and "identical" in result.output


def test_compare_refuses_a_binary_artefact(platform: Platform) -> None:
    platform.content("logo.png@1", BINARY, "image/png")
    platform.content("logo.png@2", BINARY + b"1", "image/png")
    result = _run("compare", ITEM, "logo.png@1", "logo.png@2")
    assert result.exit_code == 1 and "not text" in " ".join(result.output.split())


def test_the_runs_diff_lists_files_and_colours_their_patches(platform: Platform) -> None:
    summary = {
        "additions": 2,
        "deletions": 1,
        "base": "abc",
        "head": "def",
        "files": [
            {
                "path": "src/app.py",
                "status": "modified",
                "additions": 2,
                "deletions": 1,
                "in_scope": True,
                "patch": "@@ -1,2 +1,3 @@\n keep\n-old\n+new\n+newer",
            },
            {"path": "secrets.env", "status": "added", "additions": 0, "deletions": 0, "in_scope": False},
        ],
    }
    body = platform.contract("DiffSummary", summary)
    platform.router.get(f"{V1}/runs/run-a1/diff").mock(return_value=Response(200, json=body))
    result = _run("diff", "run-a1", env={"FORCE_COLOR": "1"})
    assert result.exit_code == 0, result.output
    plain = ANSI.sub("", result.output)
    assert "2 file(s) · +2 -1" in plain and "modified  src/app.py  +2 -1" in plain
    assert "secrets.env" in plain and "(out of scope)" in plain
    assert "\x1b[32m+new" in result.output and "\x1b[31m-old" in result.output
    assert _run("diff", "run-a1", "--json").output.count('"patch"') == 1
