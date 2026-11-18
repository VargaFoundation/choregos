"""The audit chain's walk, without a database (S80-05, ADR 0182).

The chain itself is PostgreSQL's (`test_audit_chain_postgres.py` proves the trigger, the refusals and the
backfill on a real one). What is proved here is the verifier's own logic: the canonical form does not
move, and each way of tampering with a chain is reported at the right place, for the right reason.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from tranibor_api.audit_chain import (
    GENESIS,
    ChainWalker,
    Checkpoint,
    Link,
    canonical,
    link_hash,
    read_anchors,
    sign,
)

from .kits.audit_chain import ORG, T0, TRICKY, TRICKY_HASH, new_key, unlinked


def _chain(count: int) -> list[Link]:
    """`count` entries linked as the trigger links them."""
    links: list[Link] = []
    prev = GENESIS
    for seq in range(1, count + 1):
        entry = replace(unlinked(seq, f'{{"n": {seq}}}'), prev_hash=prev)
        entry = replace(entry, hash=link_hash(prev, entry))
        links.append(entry)
        prev = entry.hash or ""
    return links


def _walk(links: list[Link], *, head: tuple[int, str] | None = None, **kwargs: object) -> ChainWalker:
    walker = ChainWalker(ORG, "tranibor", **kwargs)  # type: ignore[arg-type]
    for link in links:
        if not walker.feed(link):
            break
    walker.finish(head)
    return walker


def test_the_canonical_form_of_version_1_does_not_move() -> None:
    """The text PostgreSQL's `jsonb` renders for the entry, and the hash over it. A change here breaks every
    chain already written: a new form takes a new version tag (ADR 0182)."""
    entry = TRICKY
    assert canonical(entry) == (
        '["choregos.audit/1", "0193a8f2-0000-7000-8000-000000000001", 7, "entry-7", '
        '"2026-11-17T23:00:07.123456Z", "user", null, "project.update", null, "tab\\there", '
        '{"a": [1, 0.00000000015], "note": "é\\n\\"x\\""}]'
    )
    assert link_hash(GENESIS, entry) == TRICKY_HASH
    # The platform's entries name no organisation, and a naive time is taken as UTC.
    platform = replace(entry, org_id=None, ts=entry.ts.replace(tzinfo=None))
    assert canonical(platform).startswith(
        '["choregos.audit/1", null, 7, "entry-7", "2026-11-17T23:00:07.123456Z"'
    )


def test_an_intact_chain_is_intact() -> None:
    links = _chain(5)
    report = _walk(links, head=(5, links[-1].hash or "")).report
    assert report.intact and report.entries == 5 and report.last_seq == 5
    assert report.last_hash == links[-1].hash


def test_an_altered_entry_is_reported_where_it_is() -> None:
    links = _chain(5)
    links[2] = replace(links[2], payload='{"n": 999}')
    found = _walk(links).report.first_break
    assert found is not None and found.seq == 3 and found.entry_id == "entry-3"
    assert "altered" in found.reason


def test_a_deleted_entry_is_reported_as_missing() -> None:
    links = _chain(5)
    del links[2]
    found = _walk(links).report.first_break
    assert found is not None and found.seq == 3 and "entry 3 is missing" in found.reason


def test_an_entry_rewritten_with_its_own_hash_breaks_the_next_link() -> None:
    links = _chain(5)
    forged = replace(links[2], payload='{"n": 999}')
    links[2] = replace(forged, hash=link_hash(links[2].prev_hash or "", forged))
    found = _walk(links).report.first_break
    assert found is not None and found.seq == 4 and "does not link to entry 3" in found.reason


def test_a_place_taken_twice_is_a_fork() -> None:
    links = _chain(3)
    found = _walk([*links, links[2]]).report.first_break
    assert found is not None and found.seq == 3 and "twice" in found.reason


def test_an_entry_outside_the_chain_is_reported() -> None:
    links = _chain(2)
    found = _walk([replace(links[0], seq=None), *links]).report.first_break
    assert found is not None and found.seq is None and "outside the chain" in found.reason


def test_entries_deleted_from_the_end_are_seen_against_the_head() -> None:
    links = _chain(5)
    report = _walk(links[:3], head=(5, links[-1].hash or "")).report
    assert report.first_break is not None and report.first_break.seq == 4
    assert "deleted from the end" in report.first_break.reason
    assert not report.intact


def test_a_rewrite_from_an_entry_on_is_seen_only_against_a_signed_checkpoint() -> None:
    """Whoever rewrites entry 3 and recomputes every hash after it leaves a chain that walks clean, head
    included: the checkpoint signed before the rewrite is what sees it."""
    pem = new_key()
    links = _chain(5)
    vouched = sign(ORG, 4, links[3].hash or "", T0, pem)
    rewritten = links[:2]
    prev = links[1].hash or ""
    for original in links[2:]:
        payload = '{"n": 999}' if original.seq == 3 else original.payload
        entry = replace(original, payload=payload, prev_hash=prev)
        entry = replace(entry, hash=link_hash(prev, entry))
        rewritten.append(entry)
        prev = entry.hash or ""
    head = (5, prev)
    assert _walk(rewritten, head=head).report.intact, "without a checkpoint, the rewrite walks clean"
    found = _walk(rewritten, head=head, checkpoints=[vouched]).report.first_break
    assert found is not None and found.seq == 4 and "vouched for" in found.reason
    assert _walk(links, head=(5, links[-1].hash or ""), checkpoints=[vouched]).report.matched == [vouched]


def test_a_checkpoint_beyond_the_end_sees_a_truncation_the_head_hides() -> None:
    pem = new_key()
    links = _chain(5)
    vouched = sign(ORG, 5, links[4].hash or "", T0, pem)
    shortened = links[:4]
    report = _walk(shortened, head=(4, links[3].hash or ""), checkpoints=[vouched]).report
    assert report.first_break is not None and "deleted from the end" in report.first_break.reason


def test_a_checkpoint_whose_signature_fails_anchors_nothing_and_fails_the_verdict() -> None:
    pem = new_key()
    links = _chain(3)
    good = sign(ORG, 3, links[2].hash or "", T0, pem)
    tampered = replace(good, hash=links[1].hash or "")
    report = _walk(links, head=(3, links[2].hash or ""), checkpoints=[tampered]).report
    assert report.first_break is None and not report.intact
    assert "does not verify" in report.problems[0]


def test_a_checkpoint_signed_by_an_untrusted_key_fails_the_verdict() -> None:
    from tranibor_api.config import ed25519_public_key

    pem, other = new_key(), new_key()
    links = _chain(3)
    signed = sign(ORG, 3, links[2].hash or "", T0, pem)
    head = (3, links[2].hash or "")
    trusted = {ed25519_public_key(other)}
    report = _walk(links, head=head, checkpoints=[signed], trusted=trusted).report
    assert not report.intact and "not a trusted key" in report.problems[0]
    rotated = {*trusted, ed25519_public_key(pem)}
    assert _walk(links, head=head, checkpoints=[signed], trusted=rotated).report.intact


def test_a_checkpoint_of_another_chain_is_not_read() -> None:
    links = _chain(3)
    elsewhere = sign("another-org", 2, "f" * 64, T0, new_key())
    assert _walk(links, head=(3, links[2].hash or ""), checkpoints=[elsewhere]).report.intact


def test_an_anchor_line_reads_back_as_the_checkpoint_it_prints() -> None:
    signed = sign(ORG, 9, "a" * 64, T0, new_key())
    line = json.dumps(signed.as_anchor("tranibor"))
    (back,) = read_anchors(["", line], "kept.jsonl")
    assert back == replace(signed, source="kept.jsonl") and back.signature_holds()
    with pytest.raises(ValueError, match=r"kept\.jsonl:1"):
        read_anchors(['{"v": "something else"}'], "kept.jsonl")


def test_the_genesis_checkpoint_is_reported_not_signed() -> None:
    links = _chain(3)
    genesis = Checkpoint(chain=ORG, seq=2, hash=links[1].hash or "", kind="genesis", created_at=T0)
    report = _walk(links, head=(3, links[2].hash or ""), checkpoints=[genesis]).report
    assert report.intact and report.genesis == genesis and report.matched == []
    wrong = replace(genesis, hash="0" * 64)
    found = _walk(links, checkpoints=[wrong]).report.first_break
    assert found is not None and found.seq == 2 and "genesis" in found.reason


def test_the_commands_say_sqlite_keeps_no_chain(monkeypatch: pytest.MonkeyPatch, tmp_path: object) -> None:
    from tranibor_api.admin_cli import NO_CHAIN, app
    from tranibor_api.config import reset_settings_cache
    from typer.testing import CliRunner

    monkeypatch.setenv("CHOREGOS_DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/chain.db")
    reset_settings_cache()
    try:
        for command in (["audit", "verify"], ["audit", "checkpoint", "--key-file", "-"]):
            result = CliRunner().invoke(app, command, input="")
            assert result.exit_code == NO_CHAIN, result.output
            assert "SQLite keeps no audit chain" in result.output
    finally:
        reset_settings_cache()
