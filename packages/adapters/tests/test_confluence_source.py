"""A Confluence space or page tree as a knowledge source (S39-20), against `FakeConfluence`."""

from __future__ import annotations

from typing import Any

import pytest
from choregos_adapters.documents.base import ItemUnreadableError, SourceRefusedError
from choregos_adapters.documents.confluence import ConfluenceDocumentsConnector, ConfluenceSource, slug
from choregos_adapters.errors import UpstreamError
from choregos_adapters.fakes.confluence import CLOUD_ID, SITE_HOST, FakeConfluence
from choregos_adapters.http import rest

SITE = f"https://{SITE_HOST}"


@pytest.fixture(autouse=True)
def _no_waiting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rest, "_backoff", lambda response, attempt: 0.0)


def _site() -> FakeConfluence:
    site = FakeConfluence()
    site.add_space("ENG")
    site.put_page("ENG", "100", "Handbook", "<p>root</p>")
    site.put_page("ENG", "101", "Deploy guide", "<p>deploy with argo</p>", parent="100")
    site.put_page("ENG", "102", "Runbook", "<p>restart the pod</p>", parent="101")
    site.put_page("ENG", "103", "Unrelated", "<p>lunch menu</p>")
    return site


def _source(site: FakeConfluence, **kwargs: Any) -> ConfluenceSource:
    options: dict[str, Any] = {"space_key": "ENG", "auth": "basic"}
    options.update(kwargs)
    return ConfluenceSource(
        base_url=SITE, email=site.email, api_token=site.token, transport=site.transport(), **options
    )


async def test_a_whole_space_is_listed_in_full_with_versions() -> None:
    site = _site()
    changes = await _source(site).changes(None)
    assert changes.full is True
    assert {i.ref for i in changes.items} == {"page:100", "page:101", "page:102", "page:103"}
    deploy = next(i for i in changes.items if i.ref == "page:101")
    assert (deploy.name, deploy.version) == ("deploy-guide-101.html", "1")
    assert deploy.url == f"{SITE}/wiki/spaces/ENG/pages/101/Deploy+guide"
    site.put_page("ENG", "101", "Deploy guide", "<p>deploy with flux</p>", parent="100")
    again = await _source(site).changes(changes.cursor)
    assert next(i for i in again.items if i.ref == "page:101").version == "2"


async def test_a_page_tree_is_the_page_and_its_descendants() -> None:
    site = _site()
    whole = await _source(site, page_id="100").changes(None)
    assert {i.ref for i in whole.items} == {"page:100", "page:101", "page:102"}
    below = await _source(site, page_id="100", include_root=False).changes(None)
    assert {i.ref for i in below.items} == {"page:101", "page:102"}
    with pytest.raises(SourceRefusedError, match="page 999"):
        await _source(site, page_id="999").changes(None)


async def test_without_descendants_a_page_is_just_that_page() -> None:
    site = _site()
    one = await _source(site, page_id="100", descendants=False).changes(None)
    assert {i.ref for i in one.items} == {"page:100"}
    assert {
        i.ref for i in (await _source(site, page_ids=["101"], descendants=False).changes(None)).items
    } == {"page:101"}


async def test_chosen_pages_are_the_pages_listed_and_nothing_else() -> None:
    site = _site()
    chosen = await _source(site, page_ids=["100", "103"], descendants=False).changes(None)
    assert {i.ref for i in chosen.items} == {"page:100", "page:103"}
    # With descendants, overlapping choices list a page once.
    tree = await _source(site, page_ids=["100", "101"]).changes(None)
    assert sorted(i.ref for i in tree.items) == ["page:100", "page:101", "page:102"]


async def test_a_listed_id_that_is_missing_is_refused_by_name() -> None:
    site = _site()
    with pytest.raises(SourceRefusedError, match="page 999"):
        await _source(site, page_ids=["100", "999"]).changes(None)


async def test_a_page_that_leaves_the_chosen_set_is_no_longer_listed() -> None:
    """`changes` is a full listing: the sync engine deletes what it no longer names."""
    site = _site()
    first = await _source(site, page_ids=["101"]).changes(None)
    assert {i.ref for i in first.items} == {"page:101", "page:102"}
    site.put_page("ENG", "102", "Runbook", "<p>restart the pod</p>", parent=None)  # moved out of the tree
    again = await _source(site, page_ids=["101"]).changes(None)
    assert again.full is True and {i.ref for i in again.items} == {"page:101"}


def test_the_connector_opens_a_source_with_the_chosen_pages() -> None:
    connector = ConfluenceDocumentsConnector({"base_url": SITE, "email": "a@b.c", "api_token": "t"})
    source = connector.source({"space_key": "ENG", "page_ids": ["100", 101], "descendants": False}, key="k")
    assert (source.roots, source.descendants) == (["100", "101"], False)
    whole = connector.source({"space_key": "ENG", "page_id": "100"}, key="k")
    assert (whole.roots, whole.descendants) == (["100"], True)


def _filed() -> FakeConfluence:
    """The tree of `_site`, with a folder under the root holding pages, and a folder in that folder."""
    site = _site()
    site.put_folder("ENG", "900", "Operations", parent="100")
    site.put_page("ENG", "110", "Incidents", "<p>page the on-call</p>", parent="900")
    site.put_page("ENG", "111", "Postmortem", "<p>blameless</p>", parent="110")
    site.put_folder("ENG", "901", "Archive", parent="900")
    site.put_page("ENG", "112", "Old runbook", "<p>reboot</p>", parent="901")
    return site


def _folder_reads(site: FakeConfluence) -> list[str]:
    return [r.url.path for r in site.requests if "/folders/" in r.url.path]


async def test_a_page_tree_crosses_the_folders_under_it() -> None:
    """Folders are not in the space's page listing: the tree used to stop at the first one."""
    site = _filed()
    source = _source(site, page_id="100")
    changes = await source.changes(None)
    refs = {i.ref for i in changes.items}
    assert refs == {f"page:{n}" for n in (100, 101, 102, 110, 111, 112)}, "a folder is crossed, not listed"
    by_ref = {i.ref: i for i in changes.items}
    postmortem = (await source.fetch(by_ref["page:111"])).decode()
    assert "<p>Path: Handbook / Operations / Incidents</p>" in postmortem
    old = (await source.fetch(by_ref["page:112"])).decode()
    assert "<p>Path: Handbook / Operations / Archive</p>" in old
    assert not [p for p in _folder_reads(site) if not p.endswith("/descendants")], (
        "the folders under the root come with its descendants: none is read on its own"
    )
    below = await _source(site, page_id="100", include_root=False).changes(None)
    assert {i.ref for i in below.items} == {"page:101", "page:102", "page:110", "page:111", "page:112"}


async def test_a_chosen_page_filed_in_a_folder_keeps_its_tree_and_the_folder_in_its_path() -> None:
    site = _filed()
    source = _source(site, page_ids=["110"])
    changes = await source.changes(None)
    assert {i.ref for i in changes.items} == {"page:110", "page:111"}
    page = (await source.fetch(next(i for i in changes.items if i.ref == "page:110"))).decode()
    assert "<p>Path: Handbook / Operations</p>" in page
    alone = await _source(site, page_ids=["112"], descendants=False).changes(None)
    assert {i.ref for i in alone.items} == {"page:112"}


async def test_without_descendants_a_folder_under_the_root_is_not_crossed() -> None:
    site = _filed()
    one = await _source(site, page_id="100", descendants=False).changes(None)
    assert {i.ref for i in one.items} == {"page:100"}
    assert not any(r.url.path.endswith("/descendants") for r in site.requests)


async def test_a_whole_space_names_the_folders_in_the_path_and_lists_no_folder() -> None:
    site = _filed()
    source = _source(site)
    changes = await source.changes(None)
    assert {i.ref for i in changes.items} == {f"page:{n}" for n in (100, 101, 102, 103, 110, 111, 112)}
    page = (await source.fetch(next(i for i in changes.items if i.ref == "page:112"))).decode()
    assert "<p>Path: Handbook / Operations / Archive</p>" in page
    assert sorted(_folder_reads(site)) == ["/wiki/api/v2/folders/900", "/wiki/api/v2/folders/901"], (
        "each folder is read once per sync"
    )


async def test_a_folder_the_account_cannot_read_ends_the_path_and_does_not_fail_the_sync() -> None:
    site = _filed()
    site.restrict("900")
    source = _source(site, page_ids=["110"], descendants=False)
    changes = await source.changes(None)
    page = (await source.fetch(next(iter(changes.items)))).decode()
    assert "Path:" not in page and "Operations" not in page
    site.restrict("900", False)
    site.fail_next("/folders/900", 401)  # a scoped token without `read:folder:confluence`
    changes = await _source(site, page_ids=["110"], descendants=False).changes(None)
    assert {i.ref for i in changes.items} == {"page:110"}


async def test_a_tree_deeper_than_one_descendants_call_is_followed_to_the_end() -> None:
    """One call reaches ten levels; the tree goes on from the tenth, here a folder."""
    site = FakeConfluence()
    site.add_space("ENG")
    site.put_page("ENG", "300", "Level 0", "<p>x</p>")
    for level in range(1, 10):  # pages 301 to 309, levels 1 to 9
        site.put_page("ENG", str(300 + level), f"Level {level}", "<p>x</p>", parent=str(299 + level))
    site.put_folder("ENG", "1300", "Level 10", parent="309")
    site.put_page("ENG", "310", "Level 11", "<p>x</p>", parent="1300")
    site.put_page("ENG", "311", "Level 12", "<p>x</p>", parent="310")
    site.page_size = 3  # the descendants are paginated too, and the next link keeps its depth
    changes = await _source(site, page_id="300").changes(None)
    assert {i.ref for i in changes.items} == {f"page:{300 + n}" for n in range(12)}
    assert any(r.url.path == "/wiki/api/v2/folders/1300/descendants" for r in site.requests)


async def test_pagination_follows_the_next_links() -> None:
    site = FakeConfluence()
    site.add_space("ENG")
    for n in range(5):
        site.put_page("ENG", str(200 + n), f"Page {n}", "<p>x</p>")
    site.page_size = 2
    changes = await _source(site).changes(None)
    assert len(changes.items) == 5
    assert sum("cursor=" in str(r.url) for r in site.requests) == 2


async def test_a_restricted_deleted_or_moved_page_is_no_longer_listed() -> None:
    site = _site()
    site.restrict("102")
    assert "page:102" not in {i.ref for i in (await _source(site).changes(None)).items}
    site.delete_page("103")
    assert "page:103" not in {i.ref for i in (await _source(site).changes(None)).items}
    site.put_page("ENG", "101", "Deploy guide", "<p>moved</p>", parent=None)  # out of the tree
    tree = await _source(site, page_id="100").changes(None)
    assert {i.ref for i in tree.items} == {"page:100"}


async def test_a_listing_that_fails_raises_instead_of_looking_like_deletions() -> None:
    site = _site()
    site.fail_next("/pages", 500, times=10)
    with pytest.raises(UpstreamError):
        await _source(site).changes(None)


async def test_a_throttled_listing_is_retried() -> None:
    site = _site()
    site.fail_next("/spaces", 429, retry_after="0")
    assert len((await _source(site).changes(None)).items) == 4


async def test_a_fetch_is_the_titled_body_and_falls_back_to_storage() -> None:
    site = _site()
    site.put_page(
        "ENG", "104", "Macros", '<ac:structured-macro ac:name="info"><ac:rich-text-body><p>careful</p>'
        "</ac:rich-text-body></ac:structured-macro>", parent="100", storage_only=True,
    )  # fmt: skip
    source = _source(site, page_id="100")
    changes = await source.changes(None)
    by_ref = {i.ref: i for i in changes.items}
    page = (await source.fetch(by_ref["page:102"])).decode()
    assert "<h1>Runbook</h1>" in page and "<p>restart the pod</p>" in page
    assert "Handbook / Deploy guide" in page and f"{SITE}/wiki/spaces/ENG/pages/102/Runbook" in page
    fallback = (await source.fetch(by_ref["page:104"])).decode()
    assert "careful" in fallback and "ac:" not in fallback


async def test_a_page_restricted_since_the_listing_is_skipped_not_fatal() -> None:
    site = _site()
    source = _source(site)
    changes = await source.changes(None)
    site.restrict("103")
    with pytest.raises(ItemUnreadableError):
        await source.fetch(next(i for i in changes.items if i.ref == "page:103"))


async def test_labels_keep_only_the_pages_that_carry_one() -> None:
    site = _site()
    site.put_page("ENG", "103", "Unrelated", "<p>x</p>", labels=["lunch"])
    site.put_page("ENG", "102", "Runbook", "<p>y</p>", parent="101", labels=["Runbook-Ready"])
    changes = await _source(site, labels=["runbook-ready"]).changes(None)
    assert [i.ref for i in changes.items] == ["page:102"]


async def test_attachments_are_listed_and_downloaded_without_the_credentials() -> None:
    site = _site()
    site.add_attachment("101", "att1", "spec.pdf", b"%PDF-1.4 spec")
    site.add_attachment("101", "att2", "photo.png", b"\x89PNG", "image/png")
    source = _source(site, include_attachments=True)
    changes = await source.changes(None)
    attachment = next(i for i in changes.items if i.ref == "att:att1")
    assert (attachment.name, attachment.size) == ("spec.pdf", len(b"%PDF-1.4 spec"))
    assert "att:att2" not in {i.ref for i in changes.items}, "a format the base does not convert"
    assert await source.fetch(attachment) == b"%PDF-1.4 spec"
    media = [r for r in site.requests if r.url.host == "media.fake"]
    assert len(media) == 1 and "authorization" not in media[0].headers
    assert any(r.headers.get("authorization") for r in site.requests if r.url.host == SITE_HOST)
    off = await _source(site).changes(None)
    assert all(i.ref.startswith("page:") for i in off.items)


async def test_a_wrong_token_is_refused_and_the_connector_test_says_so() -> None:
    site = _site()
    source = ConfluenceSource(
        base_url=SITE, email=site.email, api_token="wrong", space_key="ENG", transport=site.transport()
    )
    with pytest.raises(UpstreamError) as refused:
        await source.changes(None)
    assert refused.value.status_code == 401
    from choregos_adapters.documents import confluence

    confluence.TRANSPORT = site.transport()
    try:
        bad = ConfluenceDocumentsConnector({"base_url": SITE, "email": site.email, "api_token": "wrong"})
        verdict = await bad.test()
        assert verdict["ok"] is False and "401" in verdict["reason"]
        good = ConfluenceDocumentsConnector({"base_url": SITE, "email": site.email, "api_token": site.token})
        assert await good.test() == {"ok": True, "account_spaces": 1, "cloud_id": CLOUD_ID}
    finally:
        confluence.TRANSPORT = None


async def test_a_scoped_token_goes_through_the_gateway_and_test_finds_the_cloud_id() -> None:
    site = _site()
    source = _source(site, auth="scoped", cloud_id=CLOUD_ID)
    await source.changes(None)
    assert {r.url.host for r in site.requests} == {"api.atlassian.com"}
    assert all(r.url.path.startswith(f"/ex/confluence/{CLOUD_ID}/wiki/api/v2/") for r in site.requests)

    from choregos_adapters.documents import confluence

    confluence.TRANSPORT = site.transport()
    try:
        connector = ConfluenceDocumentsConnector(
            {"base_url": SITE, "email": site.email, "api_token": site.token, "auth": "scoped"}
        )
        assert await connector.test() == {"ok": True, "account_spaces": 1, "cloud_id": CLOUD_ID}
    finally:
        confluence.TRANSPORT = None


def test_a_site_that_is_not_atlassian_cloud_is_refused() -> None:
    site = FakeConfluence()
    for url in ("http://x.atlassian.net", "https://evil.example/", "https://x.atlassian.net.evil.test"):
        with pytest.raises(SourceRefusedError):
            ConfluenceSource(
                base_url=url, email="e", api_token="t", space_key="ENG", transport=site.transport()
            )


def test_a_slug_is_a_safe_file_name() -> None:
    assert slug("Caf\u00e9 men\u00fc: na\u00efve / 2026!") == "cafe-menu-naive-2026"
    assert slug("???") == "page"
    assert len(slug("a" * 200)) <= 60
