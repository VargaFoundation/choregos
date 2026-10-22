"""The zip examples of `examples/hosted/` are what the platform accepts, and build as it would (S41-20)."""

from __future__ import annotations

import io
import pathlib
import shutil
import zipfile

import pytest
from choregos_api.services.hosted_sources import SourceRefused, base_image, read_source

from tools import hosted_dockerfile as tool

EXAMPLES = pathlib.Path(__file__).resolve().parents[2] / "examples" / "hosted"
ZIP_EXAMPLES = sorted(path.parent.name for path in EXAMPLES.glob("*/choregos.json"))


def test_the_node_and_dotnet_examples_are_zip_examples() -> None:
    assert {"reference-node", "claude-agent-sdk-node", "copilot-sdk-node", "agent-framework-dotnet"} <= set(
        ZIP_EXAMPLES
    )


@pytest.mark.parametrize("name", ZIP_EXAMPLES)
def test_a_zip_example_is_accepted_and_built_from_the_platform_s_template(name: str) -> None:
    directory = EXAMPLES / name
    archive = read_source(tool.zipped(directory))
    runtime = tool.version(directory, archive)
    assert runtime.source.type == "zip"
    text = tool.dockerfile(runtime, archive)
    assert f"FROM {base_image(runtime.source.runtime)}" in text
    assert text.endswith(f"ENTRYPOINT {tool.json.dumps(runtime.source.entry_point)}\n")
    assert not (directory / "Dockerfile").exists(), "a zip example is built by the platform's Dockerfile"


def test_the_zip_leaves_out_what_an_install_writes(tmp_path: pathlib.Path) -> None:
    shutil.copytree(EXAMPLES / "reference-node", tmp_path / "agent")
    for written in ("node_modules/x/index.js", "obj/a.json", "bin/a.dll", ".env"):
        (tmp_path / "agent" / written).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "agent" / written).write_text("x")
    names = zipfile.ZipFile(io.BytesIO(tool.zipped(tmp_path / "agent"))).namelist()
    assert "responses_server.mjs" in names and "package-lock.json" in names
    assert not [n for n in names if n.split("/")[0] in {"node_modules", "obj", "bin", ".env"}], names


def test_an_example_the_platform_would_refuse_fails_here(tmp_path: pathlib.Path) -> None:
    shutil.copytree(EXAMPLES / "reference-node", tmp_path / "agent")
    (tmp_path / "agent" / "package-lock.json").unlink()
    with pytest.raises(SourceRefused, match=r"package-lock\.json"):
        tool.version(tmp_path / "agent", read_source(tool.zipped(tmp_path / "agent")))
