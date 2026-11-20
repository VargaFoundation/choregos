"""What the build pulls from elsewhere is pinned to bytes, not to a name (S80-04).

A tag moves: `actions/checkout@v7`, `python:3.12-slim-bookworm` and `npm@latest` name whatever their
owner last pushed, so the same commit built on two days is two different builds, and a hijacked
tag or a compromised maintainer account reaches the release without a line of this repository
changing. Every reference is therefore pinned to what it resolves to:

- a GitHub Action (`uses:`) to the full 40-character commit SHA, with the version in a trailing
  comment (`@<sha> # v7.0.1`), which is what Dependabot reads to keep the pin moving;
- an image (`FROM`, `COPY --from=`, compose and workflow `image:`, `docker run`, `kind --image`, a
  workflow's `*_IMAGE` variable, a demo manifest) to its index digest (`name:tag@sha256:<digest>`);
- nothing says `latest` (`npm@latest`, `image:latest`) in a Dockerfile, a workflow, a compose file
  or a demo manifest.

Not a pin, so not checked here, each for a written reason:

- `runs-on: ubuntu-latest` is GitHub's runner label, the machine the job runs on, not an artifact
  the build pulls; the steps that matter pin what they install.
- `trials/`: each trial is the record of what was run on the day it was run (`trials/README.md`:
  "a trial is not re-run when a page is edited"); pinning it now would record a run that never
  happened. One of its images (`quay.io/minio/mc`) is no longer served at all.
- The chart's own images (`charts/choregos/values.yaml`) are this repository's releases, driven by
  their tag, and checked at admission by the chart's Kyverno policies (signature, no `latest`).
- An image this repository builds locally and never pulls: a compose service with a `build:`, a demo
  manifest with `imagePullPolicy: Never`, a Dockerfile `FROM` an argument listed in `ARGUMENT_BASES`.
"""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]

#: Directories never walked: tool caches, dependencies, other worktrees, and the trials (above).
PRUNED = {"node_modules", "__pycache__", "dist", "build", "target", "trials"}

#: `FROM ${ARG}` lines allowed, by file, with the reason. Nothing else may start from an argument.
ARGUMENT_BASES: dict[str, str] = {
    "examples/agents/langgraph/Dockerfile": "the runner image of this repository, passed by the builder",
    "examples/agents/openai-agents/Dockerfile": "the runner image of this repository, passed by the builder",
    "examples/agents/plain/Dockerfile": "the runner image of this repository, passed by the builder",
    "examples/agents/pydantic-ai/Dockerfile": "the runner image of this repository, passed by the builder",
    "plugins/investment-research/Dockerfile": (
        "the runner image of the same release, passed by digest by `release.yml` and by CI (ADR 0181 O4-3)"
    ),
    "plugins/procurement/Dockerfile": (
        "the runner image of the same release, passed by digest by `release.yml` and by CI (ADR 0198)"
    ),
}

#: Exceptions to the rules, as (file, reference): reason. Empty, and meant to stay so.
ALLOWED: dict[tuple[str, str], str] = {}

DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+@[0-9a-f]{40}$")
USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)(.*)$")
VERSION_COMMENT = re.compile(r"^\s+#\s*v?\d+(\.\d+)*\S*\s*$")
FROM = re.compile(r"^\s*FROM\s+(?:--platform=\S+\s+)?(\S+)(?:\s+AS\s+(\S+))?", re.I)
COPY_FROM = re.compile(r"(?:COPY\s+(?:--\S+\s+)*--from=|--mount=\S*?\bfrom=)([^\s,]+)", re.I)
LATEST = re.compile(r"[@:]latest\b")
#: `docker run` options that take a value as the next word.
DOCKER_RUN_VALUES = {
    "-p", "--publish", "-v", "--volume", "-e", "--env", "--name", "--network", "--entrypoint",
    "--restart", "-w", "--workdir", "--user", "-u", "--platform", "--mount", "--cap-add",
    "--cap-drop", "--security-opt", "--tmpfs", "--env-file", "-l", "--label", "--add-host",
}  # fmt: skip


def _rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _walk() -> Iterator[Path]:
    for directory, names, files in os.walk(ROOT):
        names[:] = sorted(n for n in names if not n.startswith(".") and n not in PRUNED)
        for name in sorted(files):
            yield Path(directory) / name


def dockerfiles() -> list[Path]:
    return [p for p in _walk() if p.name in {"Dockerfile", "Containerfile"} or p.name.endswith(".Dockerfile")]


def compose_files() -> list[Path]:
    return [p for p in _walk() if re.fullmatch(r"(docker-)?compose(\.[\w-]+)?\.ya?ml", p.name)]


def workflows() -> list[Path]:
    github = ROOT / ".github"
    return sorted([*github.glob("workflows/*.y*ml"), *github.glob("actions/**/action.y*ml")])


def manifests() -> list[Path]:
    """Kubernetes manifests and kind configurations the demo and the dev setup apply."""
    return sorted([*ROOT.glob("demo/**/*.yaml"), *ROOT.glob("dev/kind*.yaml")])


def _code(line: str) -> str:
    """The line without its shell comment (a whole-line one, or a `#` after a space)."""
    return "" if line.lstrip().startswith("#") else line.split(" #", 1)[0]


# --- the checks, each a pure function of a text, so the self-tests below can feed them a bad one ---


def unpinned_uses(text: str) -> list[str]:
    """Every `uses:` not pinned to a commit SHA with its version in a comment."""
    bad = []
    for line in text.splitlines():
        found = USES.match(line)
        if not found:
            continue
        ref, rest = found.group(1).strip("'\""), found.group(2)
        if ref.startswith("./"):
            continue  # an action of this repository, at this commit
        if ref.startswith("docker://"):
            if not DIGEST.search(ref):
                bad.append(ref)
            continue
        if not ACTION.match(ref) or not VERSION_COMMENT.match(rest):
            bad.append(ref)
    return bad


def unpinned_dockerfile_images(text: str, arguments_allowed: bool = False) -> list[str]:
    """Every external image a Dockerfile starts from or copies from without a digest."""
    stages: set[str] = set()
    bad = []
    for line in map(_code, text.splitlines()):
        found = FROM.match(line)
        if found:
            base, stage = found.group(1), found.group(2)
            if base.lower() == "scratch" or base in stages:
                pass
            elif base.startswith("$"):
                if not arguments_allowed:
                    bad.append(base)
            elif not DIGEST.search(base):
                bad.append(base)
            if stage:
                stages.add(stage)
            continue
        for source in COPY_FROM.findall(line):
            if source in stages or source.isdigit():
                continue
            if not DIGEST.search(source):
                bad.append(source)
    return bad


def unpinned_compose_images(document: dict[str, Any]) -> list[str]:
    bad = []
    for service in (document.get("services") or {}).values():
        image = service.get("image")
        if image is None or "build" in service:
            continue  # built here, from a Dockerfile this test checks
        if not DIGEST.search(str(image)):
            bad.append(str(image))
    return bad


def _containers(node: Any) -> Iterator[dict[str, Any]]:
    """Every mapping with an `image` key, at any depth (pod specs, job templates, kind nodes)."""
    if isinstance(node, dict):
        if isinstance(node.get("image"), str):
            yield node
        for value in node.values():
            yield from _containers(value)
    elif isinstance(node, list):
        for value in node:
            yield from _containers(value)


def unpinned_manifest_images(document: Any) -> list[str]:
    return [
        c["image"]
        for c in _containers(document)
        if c.get("imagePullPolicy") != "Never" and not DIGEST.search(c["image"])
    ]


def _workflow_image_values(workflow: dict[str, Any]) -> Iterator[str]:
    """`services:` and `container:` images, and `*_IMAGE` variables that name a registry image."""

    def env_images(env: Any) -> Iterator[str]:
        if not isinstance(env, dict):
            return
        for key, value in env.items():
            if str(key).endswith("_IMAGE") and isinstance(value, str) and "/" in value and "${{" not in value:
                yield value

    yield from env_images(workflow.get("env"))
    for job in (workflow.get("jobs") or {}).values():
        yield from env_images(job.get("env"))
        for service in (job.get("services") or {}).values():
            yield service["image"] if isinstance(service, dict) else str(service)
        container = job.get("container")
        if container:
            yield container["image"] if isinstance(container, dict) else str(container)
        for step in job.get("steps") or []:
            yield from env_images(step.get("env"))


def _run_images(script: str) -> Iterator[str]:
    """The images a shell script starts with `docker run` or gives `kind` with `--image`."""
    joined = re.sub(r"\\\n\s*", " ", script)
    for line in map(_code, joined.splitlines()):
        try:
            words = shlex.split(line, posix=True)
        except ValueError:
            words = line.split()
        for i, word in enumerate(words):
            if word == "--image" and i + 1 < len(words):
                yield words[i + 1]
            if word == "run" and i > 0 and words[i - 1] == "docker":
                rest = iter(words[i + 1 :])
                for option in rest:
                    if option.startswith("-"):
                        if option in DOCKER_RUN_VALUES:
                            next(rest, None)
                        continue
                    yield option
                    break


def unpinned_workflow_images(text: str) -> list[str]:
    workflow = yaml.safe_load(text) or {}
    found = list(_workflow_image_values(workflow))
    for job in (workflow.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            found.extend(_run_images(str(step.get("run", ""))))
    # A variable is resolved elsewhere (a `*_IMAGE` checked above, or a digest the job computed).
    return [i for i in found if "$" not in i and not DIGEST.search(i)]


def latest_references(text: str) -> list[str]:
    """`@latest` and `:latest` outside comments."""
    return [line.strip() for line in text.splitlines() if _code(line) and LATEST.search(_code(line))]


# --- the repository ---


def _offences() -> dict[str, list[str]]:
    offences: dict[str, list[str]] = {}

    def add(path: Path, refs: list[str]) -> None:
        refs = [r for r in refs if (_rel(path), r) not in ALLOWED]
        if refs:
            offences.setdefault(_rel(path), []).extend(refs)

    for path in workflows():
        text = path.read_text(encoding="utf-8")
        add(path, unpinned_uses(text) + unpinned_workflow_images(text) + latest_references(text))
    for path in dockerfiles():
        text = path.read_text(encoding="utf-8")
        add(path, unpinned_dockerfile_images(text, _rel(path) in ARGUMENT_BASES) + latest_references(text))
    for path in compose_files():
        text = path.read_text(encoding="utf-8")
        add(path, unpinned_compose_images(yaml.safe_load(text) or {}) + latest_references(text))
    for path in manifests():
        text = path.read_text(encoding="utf-8")
        refs = [i for doc in yaml.safe_load_all(text) for i in unpinned_manifest_images(doc)]
        add(path, refs + latest_references(text))
    return offences


def test_there_is_something_to_check() -> None:
    """Without this guard, moving the files would leave the suite green and empty."""
    names = {_rel(p) for p in [*workflows(), *dockerfiles(), *compose_files(), *manifests()]}
    assert {
        ".github/workflows/ci.yml",
        ".github/workflows/release.yml",
        "docker/runner.Dockerfile",
        "docker/api.Dockerfile",
        "docker/web.Dockerfile",
        "compose.yaml",
        "dev/compose.yaml",
        "demo/manifests/git.yaml",
    } <= names, sorted(names)
    assert not any(n.startswith("trials/") for n in names)


def test_every_action_image_and_tool_is_pinned() -> None:
    offences = _offences()
    assert not offences, (
        "references that move (pin an action to its commit SHA with `# vX.Y.Z`, an image to "
        "`name:tag@sha256:<index digest>`, and never `latest`):\n"
        + "\n".join(f"  {path}: {ref}" for path, refs in sorted(offences.items()) for ref in refs)
    )


def test_every_argument_base_and_exception_says_why_and_still_exists() -> None:
    for path, reason in [*ARGUMENT_BASES.items(), *((p, r) for (p, _), r in ALLOWED.items())]:
        assert reason.strip(), f"{path}: an exception without a reason"
        assert (ROOT / path).is_file(), f"{path}: a stale exception"
    for path in ARGUMENT_BASES:
        assert re.search(r"^FROM \$\{?\w+", (ROOT / path).read_text(encoding="utf-8"), re.M), (
            f"{path} no longer starts from an argument: remove it from ARGUMENT_BASES"
        )


# --- the checks catch what they claim to: each fed a reference that moves ---

SHA = "3d3c42e5aac5ba805825da76410c181273ba90b1"
DIGEST_SUFFIX = "@sha256:" + "a" * 64


def test_a_tag_a_branch_or_a_sha_without_its_version_is_an_unpinned_action() -> None:
    text = "\n".join(
        [
            "      - uses: actions/checkout@v7",
            "      - uses: pypa/gh-action-pypi-publish@release/v1",
            f"      - uses: actions/checkout@{SHA}",
            f"      - uses: actions/checkout@{SHA[:12]} # v7.0.1",
            "      - uses: docker://alpine:3.20",
            f"      - uses: actions/checkout@{SHA} # v7.0.1",
            "      - uses: ./.github/actions/setup",
            f"      - uses: docker://alpine:3.20{DIGEST_SUFFIX}",
        ]
    )
    assert unpinned_uses(text) == [
        "actions/checkout@v7",
        "pypa/gh-action-pypi-publish@release/v1",
        f"actions/checkout@{SHA}",
        f"actions/checkout@{SHA[:12]}",
        "docker://alpine:3.20",
    ]


def test_a_dockerfile_base_or_copy_source_without_a_digest_is_caught() -> None:
    text = "\n".join(
        [
            "FROM python:3.12-slim AS builder",
            "COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv",
            "RUN --mount=type=bind,from=busybox:1.37.0,target=/b true",
            f"FROM --platform=$BUILDPLATFORM golang:1.27{DIGEST_SUFFIX} AS go",
            "FROM builder AS final",
            "COPY --from=go /out /out",
            "COPY --from=0 /x /x",
            "FROM ${BASE}",
            "FROM scratch",
            "# a comment that names `COPY --from=cli`: not an instruction",
        ]
    )
    assert unpinned_dockerfile_images(text) == [
        "python:3.12-slim",
        "ghcr.io/astral-sh/uv:0.5.11",
        "busybox:1.37.0",
        "${BASE}",
    ]
    assert "${BASE}" not in unpinned_dockerfile_images(text, arguments_allowed=True)


def test_compose_manifest_and_workflow_images_without_a_digest_are_caught() -> None:
    compose = {
        "services": {
            "db": {"image": "postgres:16-alpine"},
            "ok": {"image": f"postgres:16-alpine{DIGEST_SUFFIX}"},
            "local": {"image": "choregos-api:local", "build": {"context": "."}},
        }
    }
    assert unpinned_compose_images(compose) == ["postgres:16-alpine"]
    manifest = {
        "spec": {
            "containers": [
                {"image": "alpine/git:v2.54.1"},
                {"image": "local/choregos-api:demo", "imagePullPolicy": "Never"},
            ]
        }
    }
    assert unpinned_manifest_images(manifest) == ["alpine/git:v2.54.1"]
    workflow = """
env: {TOOL_IMAGE: "moby/buildkit:v0.34.1"}
jobs:
  a:
    services: {registry: {image: "registry:2.8.3"}}
    container: node:24
    steps:
      - run: |
          docker run -d --name registry -p 5000:5000 \\
            -v "$PWD:/x" registry:2
          kind create cluster --image kindest/node:v1.28.15 --wait 1s
          docker run --rm "${IMAGE}@${DIGEST}" true
"""
    assert unpinned_workflow_images(workflow) == [
        "moby/buildkit:v0.34.1",
        "registry:2.8.3",
        "node:24",
        "registry:2",
        "kindest/node:v1.28.15",
    ]


def test_latest_is_caught_and_a_runner_label_or_a_comment_is_not() -> None:
    text = "\n".join(
        [
            " && npm install -g npm@latest pnpm@12.21.0 \\",
            "    image: quay.io/lakekeeper/catalog:latest",
            "    runs-on: ubuntu-latest",
            "        with: { args: --latest --strip header }",
            "# `orchestrator.Dockerfile` started from `choregos-api:latest`",
        ]
    )
    assert latest_references(text) == [
        "&& npm install -g npm@latest pnpm@12.21.0 \\",
        "image: quay.io/lakekeeper/catalog:latest",
    ]


def test_dependabot_moves_the_pins() -> None:
    """A pin nobody bumps rots: Dependabot covers the actions, the Dockerfiles and the compose files."""
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    covered: dict[str, set[str]] = {}
    for update in config["updates"]:
        dirs = update.get("directories") or [update["directory"]]
        covered.setdefault(update["package-ecosystem"], set()).update(dirs)
    assert "/" in covered.get("github-actions", set())
    assert {"/docker", "/examples/agents/*", "/tests/cluster/assets"} <= covered.get("docker", set())
    assert {"/", "/dev", "/tests/live/*"} <= covered.get("docker-compose", set())
