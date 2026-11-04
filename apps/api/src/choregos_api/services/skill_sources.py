# SPDX-License-Identifier: Apache-2.0
"""Skills imported from a git repository, pinned to a commit (S32-05, ADR 0050).

A repository is read at ONE commit — a 40-hex SHA, never a branch or a tag, which can move under
the import (bait-and-switch is the documented attack on skill marketplaces). A branch is resolved
to its SHA by a separate preview call, so a person sees what they pin before pinning it.

What is fetched, and how:

- only `https://github.com/<owner>/<repo>` and `https://gitlab.com/<group>/…/<project>`: no git
  clone, no other host, no credentials in the URL — the platform must not become a way to reach an
  arbitrary address (SSRF);
- the tarball of that commit, over HTTPS, with a timeout, no redirect, and a size cap;
- inside it, every folder holding a `SKILL.md` under the requested path is a candidate skill; a
  link, a device or a file that is not UTF-8 text makes that skill refused, not the repository.

The network is behind `SourceFetcher`; `FakeSourceFetcher` serves recorded tarballs to the tests
and to `CHOREGOS_FAKES=1` (ADR 0005).
"""

from __future__ import annotations

import io
import json
import posixpath
import re
import tarfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlsplit

from choregos_adapters.http.egress import guarded

from .skills import MAX_BYTES, MAX_FILES, SkillRefused, file_value, front_matter, safe_path, validate

SHA = re.compile(r"^[0-9a-f]{40}$")
HOSTS = ("github.com", "gitlab.com")
_SEGMENT = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")
#: A source of the allowlist: a host, an owner, optionally a repository (`github.com/anthropics`,
#: `github.com/anthropics/skills`, `gitlab.com/group/sub`).
SOURCE = re.compile(r"^(github\.com|gitlab\.com)(/[A-Za-z0-9_.-]{1,100}){1,6}$")
#: The default allowlist when the organisation has not written its own.
DEFAULT_SOURCES = ("github.com/anthropics/skills",)

#: A compressed tarball larger than this is not downloaded further.
MAX_TARBALL = 32 * 1024 * 1024
#: Entries read from a tarball at most, whatever their size: a repository of a million empty files
#: must not hold a worker.
MAX_ENTRIES = 50_000
#: Bytes read from the tarball's files under the requested path, all skills together.
MAX_READ = 8 * MAX_BYTES
TIMEOUT_S = 15.0
LICENCE_FILES = ("LICENSE", "LICENSE.txt", "LICENSE.md", "LICENCE", "LICENCE.txt", "LICENCE.md", "COPYING")


class SourceRefused(ValueError):  # noqa: N818 - a refusal with its reason, rendered as 422
    pass


class SourceUnavailable(RuntimeError):  # noqa: N818 - the host did not answer usefully, 502
    pass


@dataclass(frozen=True)
class GitSource:
    host: str
    #: `owner/repo` on GitHub, `group/…/project` on GitLab.
    path: str

    @property
    def url(self) -> str:
        return f"https://{self.host}/{self.path}"

    @property
    def key(self) -> str:
        """What the allowlist compares: `github.com/anthropics/skills`."""
        return f"{self.host}/{self.path}"


def parse_repo_url(url: str) -> GitSource:
    """The repository an HTTPS URL names, or a refusal: another scheme, another host, credentials,
    a query or a fragment are not a repository we fetch."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https":
        raise SourceRefused("the repository URL must be https://github.com/… or https://gitlab.com/…")
    if parts.username or parts.password or parts.port or parts.query or parts.fragment:
        raise SourceRefused("the repository URL carries no credentials, port, query or fragment")
    host = (parts.hostname or "").lower()
    if host not in HOSTS:
        raise SourceRefused(f"`{host}` is not a supported host: github.com or gitlab.com only")
    segments = [s for s in parts.path.split("/") if s]
    if segments and segments[-1].endswith(".git"):
        segments[-1] = segments[-1][: -len(".git")]
    if len(segments) < 2 or (host == "github.com" and len(segments) != 2):
        raise SourceRefused("the URL must name a repository: https://github.com/<owner>/<repo>")
    if not all(_SEGMENT.match(s) and s not in {".", ".."} for s in segments) or len(segments) > 6:
        raise SourceRefused("the repository path holds characters a repository name does not")
    return GitSource(host=host, path="/".join(segments))


def allowed(source: GitSource, sources: list[str]) -> bool:
    """Whether the repository is in the allowlist: the entry itself, or under it."""
    return any(source.key == entry or source.key.startswith(entry + "/") for entry in sources)


def folder(path: str) -> str:
    """The folder of the repository a request names, normalised; the root is ``""``."""
    text = path.strip().strip("/")
    if text in {"", "."}:
        return ""
    try:
        return safe_path(text)
    except SkillRefused as refusal:
        raise SourceRefused(str(refusal)) from refusal


# ───────────────────────────── what a tarball holds ─────────────────────────────


@dataclass
class FoundSkill:
    """A folder with a `SKILL.md`, read from the tarball, and what refuses it if anything does."""

    path: str
    files: dict[str, str] = field(default_factory=dict)
    executables: list[str] = field(default_factory=list)
    name: str | None = None
    description: str | None = None
    problem: str | None = None


@dataclass
class Snapshot:
    """A commit, as far as the import is concerned: the candidate skills and the licence files."""

    skills: list[FoundSkill]
    #: The licence files at the root of the repository, by name.
    licences: dict[str, str] = field(default_factory=dict)


def _skill_folder(path: str, folders: list[str]) -> str | None:
    """The deepest skill folder that holds a file — nested skills belong to their own folder."""
    best: str | None = None
    for candidate in folders:
        if (candidate == "" or path.startswith(candidate + "/")) and (
            best is None or len(candidate) > len(best)
        ):
            best = candidate
    return best


def _members(archive: tarfile.TarFile) -> list[tuple[str, tarfile.TarInfo]]:
    """The entries of the tarball, its top folder dropped; one that leaves the repository refuses
    the whole tarball."""
    members: list[tuple[str, tarfile.TarInfo]] = []
    for index, info in enumerate(archive):
        if index >= MAX_ENTRIES:
            raise SourceRefused(f"more than {MAX_ENTRIES} entries in the repository: refused")
        name = info.name.split("/", 1)[1] if "/" in info.name else ""
        if not name or info.isdir():
            continue
        normalised = posixpath.normpath(name)
        if normalised.startswith(("../", "/")) or normalised == "..":
            raise SourceRefused(f"`{name}` goes outside the repository: refused")
        members.append((normalised, info))
    return members


def _add_file(archive: tarfile.TarFile, skill: FoundSkill, relative: str, info: tarfile.TarInfo) -> None:
    """One file of a skill, or the reason the skill is refused."""
    if not info.isfile():
        skill.problem = f"`{relative}` is a link or a special file: refused"
        return
    if len(skill.files) >= MAX_FILES:
        skill.problem = f"more than {MAX_FILES} files"
        return
    data = archive.extractfile(info)
    if data is None:
        skill.problem = f"`{relative}` cannot be read"
        return
    try:
        skill.files[relative] = file_value(relative, data.read())
    except SkillRefused as refusal:
        skill.problem = str(refusal)
        return
    if info.mode & 0o111:
        skill.executables.append(relative)


def _check(skill: FoundSkill) -> None:
    """What a zip import checks; a refused skill still says its name when its header has one."""
    if skill.problem is None:
        try:
            head = validate(skill.files)
            skill.name, skill.description = head.name, head.description
            return
        except SkillRefused as refusal:
            skill.problem = str(refusal)
    try:
        name = front_matter(skill.files.get("SKILL.md", "")).get("name")
    except SkillRefused:
        return
    skill.name = name if isinstance(name, str) else None


def read_tarball(content: bytes, path: str) -> Snapshot:
    """The skills a commit's tarball holds under `path`, each checked like a zip import.

    The tarball's top folder (`owner-repo-sha/`) is dropped. Nothing is extracted to disk.
    """
    base = folder(path)
    try:
        archive = tarfile.open(fileobj=io.BytesIO(content), mode="r:*")  # noqa: SIM115 - closed below
    except (tarfile.TarError, EOFError, OSError) as error:
        raise SourceUnavailable("the repository tarball could not be read") from error
    with archive:
        members = _members(archive)
        licences: dict[str, str] = {}
        for name, info in members:
            if name in LICENCE_FILES and info.isfile() and info.size <= 256 * 1024:
                data = archive.extractfile(info)
                if data is not None:
                    licences[name] = data.read().decode("utf-8", errors="replace")
        under_base = [(n, i) for n, i in members if base in {"", n} or n.startswith(base + "/")]
        folders = sorted(
            {
                posixpath.dirname(n)
                for n, i in under_base
                if posixpath.basename(n) == "SKILL.md" and i.isfile()
            }
        )
        found_skills = {d: FoundSkill(path=d) for d in folders}
        bytes_read = 0
        for name, info in under_base:
            candidate = _skill_folder(name, folders)
            if candidate is None or found_skills[candidate].problem:
                continue
            bytes_read += info.size
            if bytes_read > MAX_READ:
                raise SourceRefused("the skills under this path are too large to be read")
            _add_file(
                archive, found_skills[candidate], name[len(candidate) + 1 :] if candidate else name, info
            )
    for skill in found_skills.values():
        _check(skill)
    return Snapshot(skills=list(found_skills.values()), licences=licences)


# ───────────────────────────── licence ─────────────────────────────

#: The first words of a licence text, and its SPDX identifier.
_LICENCES: tuple[tuple[str, str], ...] = (
    (r"Apache License,?\s+Version 2\.0", "Apache-2.0"),
    (r"\bMIT License\b|Permission is hereby granted, free of charge", "MIT"),
    (r"BSD 3-Clause|Neither the name of", "BSD-3-Clause"),
    (r"BSD 2-Clause|Redistribution and use in source and binary forms", "BSD-2-Clause"),
    (r"Mozilla Public License,?\s+(Version|v\.?)\s*2\.0", "MPL-2.0"),
    (r"GNU AFFERO GENERAL PUBLIC LICENSE", "AGPL-3.0"),
    (r"GNU LESSER GENERAL PUBLIC LICENSE", "LGPL-3.0"),
    (r"GNU GENERAL PUBLIC LICENSE", "GPL-3.0"),
    (r"This is free and unencumbered software", "Unlicense"),
    (r"ISC License|Permission to use, copy, modify, and/or distribute", "ISC"),
    (r"Creative Commons Attribution 4\.0|CC BY 4\.0", "CC-BY-4.0"),
    (r"CC0 1\.0|Creative Commons Zero", "CC0-1.0"),
)
_SPDX = re.compile(r"SPDX-License-Identifier:\s*([A-Za-z0-9.+-]+)")
_PROPRIETARY = re.compile(
    r"\bproprietary\b|all rights reserved|source[- ]available|may not (be )?(use|cop|distribut|modif)",
    re.IGNORECASE,
)


def detect_licence(skill_files: dict[str, str], repo_licences: dict[str, str] | None = None) -> str | None:
    """The licence of a skill: an SPDX identifier, `LicenseRef-proprietary`, or `None` when no
    licence is found. The skill's own licence file wins over the repository's."""
    for files_by_name in (skill_files, repo_licences or {}):
        for name in LICENCE_FILES:
            text = files_by_name.get(name)
            if text is None:
                continue
            if hit := _SPDX.search(text):
                return hit.group(1)
            for needle, spdx in _LICENCES:
                if re.search(needle, text, re.IGNORECASE):
                    return spdx
            if _PROPRIETARY.search(text):
                return "LicenseRef-proprietary"
            return "LicenseRef-unknown"
    skill_md = skill_files.get("SKILL.md", "")
    if hit := _SPDX.search(skill_md):
        return hit.group(1)
    return None


# ───────────────────────────── the network ─────────────────────────────


class SourceFetcher(Protocol):
    async def resolve(self, source: GitSource, ref: str, *, user_agent: str = "") -> str:
        """The full commit SHA a branch, a tag or a SHA names."""
        ...

    async def tarball(self, source: GitSource, sha: str, *, user_agent: str = "") -> bytes:
        """The tarball of the repository at that commit."""
        ...


class HttpsSourceFetcher:
    """GitHub and GitLab over HTTPS: their API resolves a reference, the tarball is downloaded with
    a timeout, without following a redirect, and stopped beyond `MAX_TARBALL`."""

    def __init__(
        self, timeout_s: float = TIMEOUT_S, max_bytes: int = MAX_TARBALL, transport: Any = None
    ) -> None:
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        #: An `httpx` transport: the tests' `MockTransport`, never the network in a unit test.
        self.transport = transport

    def _client(self, user_agent: str = "") -> Any:
        import httpx

        return httpx.AsyncClient(
            timeout=self.timeout_s,
            follow_redirects=False,
            transport=guarded(self.transport),
            headers={"User-Agent": user_agent} if user_agent else None,
        )

    def _urls(self, source: GitSource, ref: str) -> tuple[str, dict[str, str], str]:
        if source.host == "github.com":
            return (
                f"https://api.github.com/repos/{source.path}/commits/{quote(ref, safe='')}",
                {"Accept": "application/vnd.github.sha"},
                f"https://codeload.github.com/{source.path}/tar.gz/{ref}",
            )
        project = quote(source.path, safe="")
        return (
            f"https://gitlab.com/api/v4/projects/{project}/repository/commits/{quote(ref, safe='')}",
            {"Accept": "application/json"},
            f"https://gitlab.com/api/v4/projects/{project}/repository/archive.tar.gz?sha={ref}",
        )

    async def resolve(self, source: GitSource, ref: str, *, user_agent: str = "") -> str:
        import httpx

        url, headers, _ = self._urls(source, ref)
        try:
            async with self._client(user_agent) as client:
                response = await client.get(url, headers=headers)
        except httpx.HTTPError as failure:
            raise SourceUnavailable(f"{source.host} did not answer: {failure}") from failure
        if response.status_code in {404, 422}:
            raise SourceRefused(f"`{ref}` is not a branch, tag or commit of {source.url}")
        if response.status_code != 200:
            raise SourceUnavailable(f"{source.host} answered {response.status_code}")
        sha = response.text.strip() if source.host == "github.com" else str(response.json().get("id", ""))
        if not SHA.match(sha):
            raise SourceUnavailable(f"{source.host} did not give a commit SHA for `{ref}`")
        return sha

    async def tarball(self, source: GitSource, sha: str, *, user_agent: str = "") -> bytes:
        import httpx

        _, _, url = self._urls(source, sha)
        chunks: list[bytes] = []
        received = 0
        try:
            async with (
                self._client(user_agent) as client,
                client.stream("GET", url) as response,
            ):
                if response.status_code != 200:
                    raise SourceUnavailable(f"{source.host} answered {response.status_code} for the tarball")
                async for chunk in response.aiter_bytes():
                    received += len(chunk)
                    if received > self.max_bytes:
                        raise SourceRefused(
                            f"the repository tarball is larger than {self.max_bytes // (1024 * 1024)} MiB"
                        )
                    chunks.append(chunk)
        except httpx.HTTPError as failure:
            raise SourceUnavailable(f"{source.host} did not answer: {failure}") from failure
        return b"".join(chunks)


class FakeSourceFetcher:
    """Recorded repositories: references and tarballs, served without a network (ADR 0005)."""

    def __init__(self) -> None:
        self.refs: dict[str, dict[str, str]] = {}
        self.tarballs: dict[tuple[str, str], bytes] = {}
        self.calls: list[tuple[str, str, str]] = []
        #: The User-Agent each call carried, in order (ADR 0070).
        self.user_agents: list[str] = []

    def add(self, repo_url: str, sha: str, tarball: bytes, refs: dict[str, str] | None = None) -> None:
        key = parse_repo_url(repo_url).key
        self.tarballs[(key, sha)] = tarball
        self.refs.setdefault(key, {})[sha] = sha
        for name in refs or {}:
            self.refs[key][name] = (refs or {})[name]

    def load_recordings(self, directory: Path) -> None:
        """Serves the reduced tarballs the skills catalogue was generated from (`meta.json` and the
        `<owner>__<repo>__<sha12>.tar.gz` files recorded by `tools/catalogues/build_skill_catalogue.py`)."""
        meta = json.loads((directory / "meta.json").read_text(encoding="utf-8"))
        for repo, recorded in meta["repos"].items():
            owner, _, name = repo.partition("/")
            sha = recorded["sha"]
            tarball = (directory / f"{owner}__{name}__{sha[:12]}.tar.gz").read_bytes()
            self.add(f"https://github.com/{repo}", sha, tarball, refs={recorded["ref"]: sha})

    async def resolve(self, source: GitSource, ref: str, *, user_agent: str = "") -> str:
        self.calls.append(("resolve", source.key, ref))
        self.user_agents.append(user_agent)
        if source.key not in self.refs:
            raise SourceUnavailable(f"{source.url} is not a recorded repository")
        sha = self.refs[source.key].get(ref)
        if sha is None:
            raise SourceRefused(f"`{ref}` is not a branch, tag or commit of {source.url}")
        return sha

    async def tarball(self, source: GitSource, sha: str, *, user_agent: str = "") -> bytes:
        self.calls.append(("tarball", source.key, sha))
        self.user_agents.append(user_agent)
        content = self.tarballs.get((source.key, sha))
        if content is None:
            raise SourceUnavailable(f"no recorded tarball for {source.url}@{sha}")
        return content


_FETCHER: dict[str, SourceFetcher] = {}


def set_fetcher(fetcher: SourceFetcher | None) -> None:
    """Replaces the fetcher (the tests); `None` goes back to the default."""
    _FETCHER.clear()
    if fetcher is not None:
        _FETCHER["current"] = fetcher


def get_fetcher(fakes: bool) -> SourceFetcher:
    """The fetcher set, else a recorded one under `CHOREGOS_FAKES=1`, else HTTPS."""
    if "current" not in _FETCHER:
        _FETCHER["current"] = FakeSourceFetcher() if fakes else HttpsSourceFetcher()
    return _FETCHER["current"]


__all__ = [
    "DEFAULT_SOURCES",
    "SHA",
    "SOURCE",
    "FakeSourceFetcher",
    "FoundSkill",
    "GitSource",
    "HttpsSourceFetcher",
    "Snapshot",
    "SourceFetcher",
    "SourceRefused",
    "SourceUnavailable",
    "allowed",
    "detect_licence",
    "folder",
    "get_fetcher",
    "parse_repo_url",
    "read_tarball",
    "set_fetcher",
]
