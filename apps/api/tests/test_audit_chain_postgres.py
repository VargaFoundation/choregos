"""The audit log is a hash chain the database keeps, and the API's role cannot change (S80-05, ADR 0182).

PostgreSQL only (`TRANIBOR_TEST_DATABASE_URL`): the schema of `python -m tranibor_api.migrate`, the
non-superuser role the API connects as, which OWNS the tables (as the chart's Job leaves them), under
forced row-level security. The tampering is done as the superuser with the triggers off
(`session_replication_role = replica`): what someone who holds the database itself can do, and what only
the chain can show.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from .conftest import APP_ROLE, PG_URL, _administer, _app_url, _prepare_postgres, without_postgres
from .kits.audit_chain import TRICKY, TRICKY_HASH, new_key

pytestmark = without_postgres

PLATFORM = ""


async def _superuser(*statements: str) -> None:
    """As the superuser, triggers off: the hand of whoever holds the database."""
    await _administer(PG_URL, "SET session_replication_role = replica", *statements)


async def _fetch(query: str) -> list[tuple[Any, ...]]:
    import asyncpg

    connection = await asyncpg.connect(PG_URL.replace("postgresql+asyncpg://", "postgresql://"))
    try:
        return [tuple(row) for row in await connection.fetch(query)]
    finally:
        await connection.close()


@pytest.fixture
async def orgs(pg_app: Any) -> dict[str, str]:
    """Organisations `a` and `b`: slug -> id."""
    from tranibor_api.db.models import Organization
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        rows = [Organization(slug=slug, name=slug.upper()) for slug in ("a", "b")]
        session.add_all(rows)
        await session.flush()
        return {row.slug: row.id for row in rows}


async def _write(scope: Any, org_id: str | None, action: str, **payload: Any) -> None:
    from tranibor_api.audit import record
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs=scope) as session:
        await record(session, None, action, org_id=org_id, target_type="test", target_id=action, **payload)


async def _verify(chain: str, label: str = "x", **kwargs: Any) -> Any:
    from tranibor_api.audit_chain import verify
    from tranibor_api.db.session import session_scope

    async with session_scope(orgs="*") as session:
        return await verify(session, chain, label, **kwargs)


async def test_postgres_hashes_an_entry_as_the_verifier_does(pg_app: Any) -> None:
    """The database's function and the verifier's code are two writings of ADR 0182's canonical form: they
    agree on an entry made to trip one of them (a null, a tab, an accent, quotes, a long number)."""
    from tranibor_api.audit_chain import GENESIS

    [(computed,)] = await _fetch(
        "SELECT choregos_audit_hash("
        f"'{GENESIS}', '{TRICKY.org_id}', {TRICKY.seq}, '{TRICKY.id}', '{TRICKY.ts.isoformat()}', "
        f"'{TRICKY.actor_kind}', NULL, '{TRICKY.action}', NULL, E'tab\\there', "
        """'{"note": "é\\n\\"x\\"", "a": [1, 1.5e-10]}'::jsonb)"""
    )
    assert computed == TRICKY_HASH


async def test_every_entry_is_linked_in_its_own_chain(
    orgs: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Whoever writes, through `audit.record`: each organisation's entries and the platform's form a chain
    of their own, 1, 2, 3..., that the verifier walks clean, two entries a page. A session bounded to `a`
    that writes a platform entry (a sign-in) links it to the platform's chain, which it cannot read back."""
    from tranibor_api import audit_chain

    monkeypatch.setattr(audit_chain, "PAGE", 2)
    for n in range(3):
        await _write(["a"], orgs["a"], "a.thing", n=n, note='é\n"quoted"', ratio=0.1 + 0.2)
        await _write(["b"], orgs["b"], "b.thing", n=n)
    await _write(["a"], None, "auth.login", who="u-a")
    await _write("*", None, "platform.thing", nested={"z": [1, {"y": None}], "a": True})

    rows = await _fetch("SELECT coalesce(org_id, ''), seq FROM audit_log ORDER BY 1, 2")
    assert rows == [
        ("", 1),
        ("", 2),
        *sorted((orgs[slug], seq) for slug in ("a", "b") for seq in (1, 2, 3)),
    ]
    for chain in (orgs["a"], orgs["b"], PLATFORM):
        report = await _verify(chain)
        assert report.intact, report.as_dict()
        assert report.entries == (2 if chain == PLATFORM else 3)


async def test_the_api_role_cannot_update_delete_or_truncate_the_log(orgs: dict[str, str]) -> None:
    """The plan's acceptance: a modification of the log by the API's role is refused. That role owns the
    tables (the chart's Job migrates as it): `UPDATE` and `TRUNCATE` are revoked from it, and a `DELETE`
    of an organisation that still exists is refused by the trigger."""
    from tranibor_api.db.session import session_scope

    await _write(["a"], orgs["a"], "a.thing")
    for statement, refusal in (
        ("UPDATE audit_log SET action = 'forged'", "permission denied"),
        ("UPDATE audit_checkpoints SET hash = 'x'", "permission denied"),
        ("DELETE FROM audit_log", "append-only"),
        ("TRUNCATE audit_log", "permission denied"),
        ("TRUNCATE audit_checkpoints", "permission denied"),
    ):
        with pytest.raises(DBAPIError, match=refusal):
            async with session_scope(orgs="*") as session:
                await session.execute(text(statement))
    privileges = await _fetch(
        f"SELECT has_table_privilege('{APP_ROLE}', t, p) "
        "FROM unnest(ARRAY['audit_log', 'audit_checkpoints']) t, unnest(ARRAY['UPDATE', 'TRUNCATE']) p"
    )
    assert privileges == [(False,)] * 4
    assert await _fetch("SELECT action FROM audit_log") == [("a.thing",)]


async def test_the_trigger_refuses_an_update_even_when_the_privilege_is_granted_back(
    orgs: dict[str, str],
) -> None:
    """Two locks, each enough alone: with `UPDATE` granted back to the API's role (a `GRANT` the
    application never sends), the trigger still refuses."""
    from tranibor_api.admin_cli import checkpoint_chains
    from tranibor_api.db.session import session_scope

    await _write(["a"], orgs["a"], "a.thing")
    await checkpoint_chains(["a"], False, new_key(), [])
    await _administer(PG_URL, f"GRANT UPDATE ON audit_log, audit_checkpoints TO {APP_ROLE}")
    try:
        for table in ("audit_log", "audit_checkpoints"):
            with pytest.raises(DBAPIError, match="append-only"):
                async with session_scope(orgs="*") as session:
                    await session.execute(text(f"UPDATE {table} SET seq = seq"))
    finally:
        await _administer(PG_URL, f"REVOKE UPDATE ON audit_log, audit_checkpoints FROM {APP_ROLE}")


async def test_an_organisation_deleted_takes_its_chain_and_leaves_the_others_intact(
    orgs: dict[str, str],
) -> None:
    """The enterprise edition's end of life deletes an organisation (`DELETE /orgs/{org}`), its entries
    going with it by the foreign key, then writes a platform entry. The one `DELETE` the log lets through."""
    from tranibor_api.db.models import Organization
    from tranibor_api.db.session import session_scope

    for slug in ("a", "b"):
        await _write([slug], orgs[slug], f"{slug}.thing")
    await _write("*", None, "platform.thing")
    async with session_scope(orgs="*") as session:
        organisation = await session.get(Organization, orgs["b"])
        await session.delete(organisation)
        await session.flush()
        from tranibor_api.audit import record

        await record(session, None, "org.delete", org_id=None, target_type="org", target_id=orgs["b"])

    assert await _fetch(f"SELECT count(*) FROM audit_log WHERE org_id = '{orgs['b']}'") == [(0,)]
    assert await _fetch(f"SELECT count(*) FROM audit_chain_heads WHERE chain = '{orgs['b']}'") == [(0,)]
    assert (await _verify(orgs["a"])).intact
    platform = await _verify(PLATFORM)
    assert platform.intact and platform.entries == 2


async def test_the_verifier_finds_an_altered_entry(orgs: dict[str, str]) -> None:
    for n in range(4):
        await _write(["a"], orgs["a"], "a.thing", n=n)
    await _superuser(
        f"UPDATE audit_log SET payload = '{{\"n\": 99}}' WHERE org_id = '{orgs['a']}' AND seq = 2"
    )
    report = await _verify(orgs["a"])
    assert report.first_break is not None and report.first_break.seq == 2
    assert "altered" in report.first_break.reason and report.entries == 1


async def test_the_verifier_finds_a_deleted_entry_and_a_deleted_end(orgs: dict[str, str]) -> None:
    for n in range(5):
        await _write(["a"], orgs["a"], "a.thing", n=n)
    await _superuser(f"DELETE FROM audit_log WHERE org_id = '{orgs['a']}' AND seq = 3")
    found = (await _verify(orgs["a"])).first_break
    assert found is not None and found.seq == 3 and "missing" in found.reason

    for n in range(2):
        await _write(["b"], orgs["b"], "b.thing", n=n)
    await _superuser(f"DELETE FROM audit_log WHERE org_id = '{orgs['b']}' AND seq = 2")
    found = (await _verify(orgs["b"])).first_break
    assert found is not None and "deleted from the end" in found.reason


async def test_a_rewrite_with_every_hash_recomputed_is_seen_against_the_signed_checkpoint(
    orgs: dict[str, str],
) -> None:
    """Someone who holds the database rewrites entry 2 and recomputes every hash after it and the head,
    with the database's own function: the chain walks clean, and only the checkpoint signed before, with a
    key the database does not hold, sees it."""
    from tranibor_api.admin_cli import checkpoint_chains, verify_chains
    from tranibor_api.config import ed25519_public_key

    for n in range(3):
        await _write(["a"], orgs["a"], "a.thing", n=n)
    pem = new_key()
    [(report, signed)] = await checkpoint_chains(["a"], False, pem, [])
    assert report.intact and signed is not None and signed.seq == 3 and signed.signature_holds()
    assert await _fetch("SELECT kind, seq FROM audit_checkpoints") == [("signed", 3)]

    await _superuser(
        f"""
        DO $$ DECLARE r record; prev text := repeat('0', 64); h text; BEGIN
          FOR r IN SELECT * FROM audit_log WHERE org_id = '{orgs["a"]}' ORDER BY seq LOOP
            IF r.seq = 2 THEN r.payload := '{{"n": 99}}'; END IF;
            h := choregos_audit_hash(prev, r.org_id, r.seq, r.id, r.ts, r.actor_kind, r.actor_id, r.action,
                                     r.target_type, r.target_id, r.payload);
            UPDATE audit_log SET payload = r.payload, prev_hash = prev, hash = h WHERE id = r.id;
            prev := h;
          END LOOP;
          UPDATE audit_chain_heads SET hash = prev WHERE chain = '{orgs["a"]}';
        END $$
        """
    )
    await _superuser("DELETE FROM audit_checkpoints")  # the rewriter removes the stored checkpoint too
    assert (await _verify(orgs["a"])).intact, "the chain alone walks clean after a full rewrite"
    [kept] = await verify_chains(["a"], False, [ed25519_public_key(pem)], [])
    assert kept.intact, "nothing outside the database: nothing sees it"

    anchor = await asyncio.to_thread(_anchor_file, signed.as_anchor("a"))
    [seen] = await verify_chains(["a"], False, [ed25519_public_key(pem)], [anchor])
    assert seen.first_break is not None and seen.first_break.seq == 3
    assert "vouched for" in seen.first_break.reason


def _anchor_file(line: dict[str, Any]) -> Any:
    import json
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp(prefix="anchor-")) / "kept.jsonl"
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    return path


async def test_a_checkpoint_stored_in_the_database_sees_a_rewrite_that_left_it(orgs: dict[str, str]) -> None:
    from tranibor_api.admin_cli import checkpoint_chains

    for n in range(3):
        await _write(["a"], orgs["a"], "a.thing", n=n)
    await checkpoint_chains(["a"], False, new_key(), [])
    await _superuser(
        f"UPDATE audit_log SET payload = '{{\"n\": 99}}' WHERE org_id = '{orgs['a']}' AND seq = 3",
        f"UPDATE audit_log SET hash = choregos_audit_hash(prev_hash, org_id, seq, id, ts, actor_kind, "
        f"actor_id, action, target_type, target_id, payload) WHERE org_id = '{orgs['a']}' AND seq = 3",
        f"UPDATE audit_chain_heads SET hash = (SELECT hash FROM audit_log WHERE org_id = '{orgs['a']}' "
        f"AND seq = 3) WHERE chain = '{orgs['a']}'",
    )
    found = (await _verify(orgs["a"])).first_break
    assert found is not None and found.seq == 3 and "vouched for" in found.reason


async def test_concurrent_writers_keep_one_chain(orgs: dict[str, str]) -> None:
    """Twenty transactions write to the same organisation at once, each holding its transaction open after
    its insert: the head's lock puts them in a line, and the chain has twenty places, none twice."""
    from tranibor_api.audit import record
    from tranibor_api.db.session import session_scope

    async def writer(n: int) -> None:
        async with session_scope(orgs=["a"]) as session:
            await record(session, None, "a.concurrent", org_id=orgs["a"], target_type="test", n=n)
            await record(session, None, "auth.login", org_id=None, target_type="test", n=n)
            await session.flush()
            await session.execute(text("SELECT pg_sleep(0.05)"))

    await asyncio.gather(*(writer(n) for n in range(20)))
    for chain in (orgs["a"], PLATFORM):
        report = await _verify(chain)
        assert report.intact, report.as_dict()
        assert report.entries == 20
    assert await _fetch(
        f"SELECT count(DISTINCT seq), min(seq), max(seq) FROM audit_log WHERE org_id = '{orgs['a']}'"
    ) == [(20, 1, 20)]


async def test_checkpoints_are_read_like_the_log_and_written_by_the_platform_only(
    orgs: dict[str, str],
) -> None:
    from tranibor_api.admin_cli import checkpoint_chains
    from tranibor_api.db.models import AuditCheckpoint
    from tranibor_api.db.session import session_scope

    for slug in ("a", "b"):
        await _write([slug], orgs[slug], f"{slug}.thing")
    await checkpoint_chains([], True, new_key(), [])
    async with session_scope(orgs=["a"]) as session:
        seen = (await session.execute(select(AuditCheckpoint.org_id))).scalars().all()
        assert seen == [orgs["a"]]
    with pytest.raises(DBAPIError, match="row-level security"):
        async with session_scope(orgs=["a"]) as session:
            session.add(
                AuditCheckpoint(
                    org_id=orgs["a"], seq=1, hash="0" * 64, kind="signed", created_at=datetime.now(UTC)
                )
            )


async def test_a_rotation_signs_on_top_only_of_the_keys_it_is_told_to_trust(orgs: dict[str, str]) -> None:
    """After a rotation, the new key does not sign over a checkpoint of the former one unless the former one
    is named (`--trust`): nothing is ever signed on top of a checkpoint someone else made."""
    from tranibor_api.admin_cli import checkpoint_chains
    from tranibor_api.config import ed25519_public_key

    former, current = new_key(), new_key()
    await _write(["a"], orgs["a"], "a.thing")
    await checkpoint_chains(["a"], False, former, [])
    await _write(["a"], orgs["a"], "a.thing")
    [(refused, none)] = await checkpoint_chains(["a"], False, current, [])
    assert none is None and not refused.intact and "not a trusted key" in refused.problems[0]
    [(report, signed)] = await checkpoint_chains(["a"], False, current, [ed25519_public_key(former)])
    assert report.intact and signed is not None and signed.seq == 2


# ───────────────────────── the history that exists, chained by the migration ─────────────────────────

#: The revision before the chain.
BEFORE = "e7b9d1f3a5c8"


async def test_the_migration_chains_the_history_that_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """Entries written before the chain existed are chained once by the upgrade, as the API's role under
    forced row-level security, with a genesis checkpoint per chain; the next entry follows them."""
    from tranibor_api.config import reset_settings_cache
    from tranibor_api.db import session as db_session
    from tranibor_api.migrate import configuration

    await _prepare_postgres()
    monkeypatch.setenv("TRANIBOR_DATABASE_URL", _app_url(PG_URL))
    reset_settings_cache()
    await db_session.dispose_engine()
    config = configuration()
    try:
        await asyncio.to_thread(command.upgrade, config, BEFORE)
        await _administer(
            PG_URL,
            "INSERT INTO organizations (id, slug, name, settings) VALUES ('o1', 'a', 'A', '{}')",
            *(
                "INSERT INTO audit_log (id, org_id, actor_kind, action, payload, ts) VALUES "
                f"('e{n}', {org}, 'user', 'old.thing', '{{\"n\": {n}}}', "
                f"'2026-11-0{n}T10:00:00+00')"
                for n, org in ((3, "'o1'"), (1, "'o1'"), (2, "NULL"), (4, "'o1'"))
            ),
        )
        await asyncio.to_thread(command.upgrade, config, "heads")
        assert await _fetch("SELECT id, seq FROM audit_log ORDER BY coalesce(org_id, ''), seq") == [
            ("e2", 1),
            ("e1", 1),
            ("e3", 2),
            ("e4", 3),
        ]
        assert sorted(await _fetch("SELECT coalesce(org_id, ''), seq, kind FROM audit_checkpoints")) == [
            ("", 1, "genesis"),
            ("o1", 3, "genesis"),
        ]
        await _write("*", "o1", "new.thing")
        report = await _verify("o1", "a")
        assert report.intact and report.entries == 4
        assert report.genesis is not None and report.genesis.seq == 3
        assert (await _verify(PLATFORM)).intact
    finally:
        await db_session.dispose_engine()
        await _administer(PG_URL, "DROP SCHEMA public CASCADE", "CREATE SCHEMA public")
        reset_settings_cache()
