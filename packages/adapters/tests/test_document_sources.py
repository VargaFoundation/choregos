"""Knowledge base sources (S33-04): Graph delta, an allow-listed crawl, a git repository, and the fake."""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from choregos_adapters.documents import GitSource, GraphDriveSource, WebCrawlSource
from choregos_adapters.documents.base import SourceRefusedError
from choregos_adapters.fakes.documents import FakeDocumentSource

GRAPH = "https://graph.microsoft.com/v1.0"


def _graph_transport(pages: dict[str, dict[str, Any]], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        url = str(request.url)
        if url.endswith("/oauth2/v2.0/token"):
            assert b"scope=https%3A%2F%2Fgraph.microsoft.com%2F.default" in request.content
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if url.endswith("/content"):
            return httpx.Response(302, headers={"location": "https://download.sharepoint.test/blob?sig=x"})
        if url.startswith("https://download.sharepoint.test/"):
            return httpx.Response(200, content=b"%PDF-1.4 bytes")
        if url in pages:
            return httpx.Response(200, json=pages[url])
        return httpx.Response(404, json={"error": url})

    return httpx.MockTransport(handler)


async def test_graph_delta_lists_files_deletions_and_keeps_the_delta_link() -> None:
    first = f"{GRAPH}/drives/d1/root/delta"
    pages = {
        f"{GRAPH}/sites/s1/drive": {"id": "d1"},
        first: {
            "value": [
                {"id": "root", "root": {}, "folder": {}},
                {"id": "f1", "name": "zoning.pptx", "file": {}, "cTag": "c1", "webUrl": "https://sp/zoning.pptx",
                 "parentReference": {"path": "/drive/root:/Policies"}, "size": 10},
                {"id": "f2", "name": "other.docx", "file": {}, "cTag": "c2",
                 "parentReference": {"path": "/drive/root:/Elsewhere"}},
            ],
            "@odata.nextLink": f"{GRAPH}/drives/d1/root/delta?token=p2",
        },
        f"{GRAPH}/drives/d1/root/delta?token=p2": {
            "value": [{"id": "f9", "deleted": {"state": "deleted"}}],
            "@odata.deltaLink": f"{GRAPH}/drives/d1/root/delta?token=next",
        },
    }  # fmt: skip
    seen: list[httpx.Request] = []
    source = GraphDriveSource(
        tenant_id="t", client_id="c", client_secret="s", site_id="s1", folder="Policies",
        transport=_graph_transport(pages, seen),
    )  # fmt: skip
    changes = await source.changes(None)
    assert [(i.ref, i.name, i.version) for i in changes.items] == [("f1", "zoning.pptx", "c1")]
    assert changes.deleted == ["f2", "f9"], "outside the folder, or deleted: it leaves the base"
    assert changes.cursor == {"delta_link": f"{GRAPH}/drives/d1/root/delta?token=next", "drive_id": "d1"}
    assert not changes.full

    data = await source.fetch(changes.items[0])
    assert data == b"%PDF-1.4 bytes"
    download = next(r for r in seen if r.url.host == "download.sharepoint.test")
    assert "authorization" not in download.headers, "the bearer never leaves Graph"


async def test_graph_resumes_from_its_delta_link() -> None:
    link = f"{GRAPH}/drives/d1/root/delta?token=next"
    pages = {link: {"value": [], "@odata.deltaLink": f"{GRAPH}/drives/d1/root/delta?token=after"}}
    seen: list[httpx.Request] = []
    source = GraphDriveSource(
        tenant_id="t",
        client_id="c",
        client_secret="s",
        drive_id="d1",
        transport=_graph_transport(pages, seen),
    )
    changes = await source.changes({"delta_link": link})
    assert changes.cursor is not None and changes.cursor["delta_link"].endswith("token=after")
    assert [str(r.url) for r in seen if "delta" in str(r.url)] == [link]


def _site(pages: dict[str, tuple[int, str]], robots: str = "") -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text=robots)
        status, body = pages.get(url, (404, ""))
        return httpx.Response(status, text=body, headers={"content-type": "text/html; charset=utf-8"})

    return httpx.MockTransport(handler)


async def _public(host: str) -> list[str]:
    return ["93.184.216.34"]


async def test_a_crawl_stays_on_its_site_obeys_robots_and_caps_depth() -> None:
    root = "https://docs.example.com/guide/"
    pages = {
        root: (200, '<a href="a.html">a</a><a href="private/x.html">x</a><a href="https://evil.test/">e</a>'
                    '<a href="/other/">out of prefix</a>'),
        f"{root}a.html": (200, '<a href="b.html">b</a>'),
        f"{root}b.html": (200, "too deep"),
        f"{root}private/x.html": (200, "disallowed"),
    }  # fmt: skip
    crawl = WebCrawlSource(
        url=root,
        max_depth=1,
        transport=_site(pages, "User-agent: *\nDisallow: /guide/private/"),
        resolver=_public,
    )
    changes = await crawl.changes(None)
    assert [i.ref for i in changes.items] == [root, f"{root}a.html"]
    assert changes.full and changes.items[0].name == "guide.html"
    assert await crawl.fetch(changes.items[1]) == pages[f"{root}a.html"][1].encode()


async def test_a_server_error_is_unreadable_not_deleted() -> None:
    root = "https://docs.example.com/"
    crawl = WebCrawlSource(
        url=root, transport=_site({root: (200, '<a href="a.html">a</a>'), f"{root}a.html": (503, "")}),
        resolver=_public,
    )  # fmt: skip
    changes = await crawl.changes(None)
    assert changes.unreadable == [f"{root}a.html"]


def _redirecting(routes: dict[str, tuple[int, str, str | None]]) -> httpx.MockTransport:
    """url -> (status, body, location)."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/robots.txt"):
            return httpx.Response(200, text="")
        status, body, location = routes.get(url, (404, "", None))
        headers = {"content-type": "text/html"}
        if location:
            headers["location"] = location
        return httpx.Response(status, text=body, headers=headers)

    return httpx.MockTransport(handler)


async def test_a_crawl_excludes_paths_and_names_pages_by_their_whole_path() -> None:
    root = "https://example.test/"
    pages = {
        root: (200, '<a href="/en/about">en</a><a href="/es/about">es</a><a href="/es">es</a>'),
        f"{root}en/about": (200, "english"),
        f"{root}es/about": (200, "spanish"),
        f"{root}es": (200, "spanish home"),
    }
    crawl = WebCrawlSource(url=root, exclude=["/es", "/es/*"], transport=_site(pages), resolver=_public)
    changes = await crawl.changes(None)
    assert [i.name for i in changes.items] == ["index.html", "en-about.html"]
    assert not changes.unreadable


async def test_a_redirect_inside_the_allow_list_is_followed() -> None:
    root = "https://example.test/"
    routes = {
        root: (200, '<a href="/en/">en</a>', None),
        f"{root}en/": (301, "", "/en"),
        f"{root}en": (200, "english", None),
    }
    changes = await WebCrawlSource(url=root, transport=_redirecting(routes), resolver=_public).changes(None)
    assert [(i.ref, i.name) for i in changes.items] == [(root, "index.html"), (f"{root}en", "en.html")]


async def test_a_crawl_that_lists_nothing_is_unreadable_not_empty() -> None:
    routes = {"https://www.example.test/": (301, "", "https://example.test/")}
    crawl = WebCrawlSource(url="https://www.example.test/", transport=_redirecting(routes), resolver=_public)
    changes = await crawl.changes({"pages": 3})
    assert changes.items == [] and changes.unreadable == ["https://www.example.test/"]


async def test_a_start_that_redirects_off_the_allow_list_is_refused_with_its_target() -> None:
    routes = {"https://www.example.test/": (301, "", "https://example.test/")}
    crawl = WebCrawlSource(url="https://www.example.test/", transport=_redirecting(routes), resolver=_public)
    with pytest.raises(SourceRefusedError, match=r"redirects to `https://example.test/`: start there"):
        await crawl.check_start()


@pytest.mark.parametrize(
    ("url", "addresses", "reason"),
    [
        ("http://docs.example.com/", ["93.184.216.34"], "https"),
        ("https://intranet.local/", ["10.0.0.4"], "private address"),
        ("https://metadata.test/", ["169.254.169.254"], "private address"),
        ("https://localhost/", ["127.0.0.1"], "private address"),
    ],
)
async def test_a_crawl_does_not_reach_into_the_network(url: str, addresses: list[str], reason: str) -> None:
    async def resolver(host: str) -> list[str]:
        return addresses

    with pytest.raises(SourceRefusedError, match=reason):
        crawl = WebCrawlSource(url=url, resolver=resolver, transport=_site({}))
        await crawl.changes(None)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=cwd, check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip


async def test_a_git_source_lists_its_markdown_and_follows_its_branch(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    (repo / "docs").mkdir()
    (repo / "docs" / "zoning.md").write_text("# Zoning\n")
    (repo / "README.md").write_text("# Readme\n")
    (repo / "app.py").write_text("print(1)\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "one")

    source = GitSource(url=f"file://{repo}", ref="main", paths=["docs/*.md"], allow_file=True)
    first = await source.changes(None)
    assert [i.ref for i in first.items] == ["docs/zoning.md"] and first.full
    assert await source.fetch(first.items[0]) == b"# Zoning\n"
    same = await source.changes(first.cursor)
    assert same.items == [] and not same.full, "the same commit is no change"

    (repo / "docs" / "zoning.md").unlink()
    (repo / "docs" / "dmz.md").write_text("# DMZ\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "two")
    after = await source.changes(first.cursor)
    assert [i.ref for i in after.items] == ["docs/dmz.md"] and after.full
    source.close()


@pytest.mark.parametrize(
    ("url", "ref"), [("http://git.test/r.git", "main"), ("https://git.test/r.git", "--upload-pack=x")]
)
def test_a_git_source_is_https_and_a_ref_is_not_an_option(url: str, ref: str) -> None:
    with pytest.raises(SourceRefusedError):
        GitSource(url=url, ref=ref)


async def test_the_fake_source_answers_like_a_delta() -> None:
    fake = FakeDocumentSource()
    fake.put("a", "a.md", b"# A\n")
    fake.put("b", "b.md", b"# B\n")
    first = await fake.changes(None)
    assert [i.ref for i in first.items] == ["a", "b"]
    fake.delete("a")
    fake.put("b", "b.md", b"# B2\n")
    second = await fake.changes(first.cursor)
    assert ([i.ref for i in second.items], second.deleted) == (["b"], ["a"])
    assert await fake.fetch(second.items[0]) == b"# B2\n"
    assert asyncio.iscoroutinefunction(fake.changes)


def test_a_documents_connector_opens_a_source_from_its_configuration() -> None:
    from choregos_adapters.documents.base import DocumentsConnector
    from choregos_adapters.documents.graph import GraphDocumentsConnector
    from choregos_adapters.fakes.documents import FakeDocumentsConnector, fake_source

    graph: DocumentsConnector = GraphDocumentsConnector({"tenant_id": "t", "client_id": "c"})
    opened = graph.source({"site_id": "s1", "drive_id": "d1", "folder": "/Policies/"}, key="src1")
    assert isinstance(opened, GraphDriveSource)
    assert (opened.site_id, opened.drive_id, opened.folder) == ("s1", "d1", "/Policies")
    with pytest.raises(ValueError, match="site_id"):
        graph.source({}, key="src2")

    fake: DocumentsConnector = FakeDocumentsConnector({})
    assert fake.source({"anything": 1}, key="src3") is fake_source("src3")
