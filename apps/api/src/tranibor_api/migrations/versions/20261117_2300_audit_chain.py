# SPDX-License-Identifier: Apache-2.0
"""The audit log is a hash chain per organisation, and the database refuses to change it (S80-05, ADR 0182)

`audit_log` was a plain table: a row could be updated or deleted by the API's own role and nothing would
show it. From this revision, on PostgreSQL:

- **every entry is a link**: a `BEFORE INSERT` trigger (`choregos_audit_link`) gives it its place in its
  chain (`seq`, one chain per organisation and one for the platform's entries, `org_id IS NULL`), the hash
  of the entry before it (`prev_hash`) and its own `hash = sha256(prev_hash || canonical(entry))`. The
  chain's last link is kept in `audit_chain_heads`, whose row the trigger locks until the transaction
  ends: two writers of one chain are put in a line, whatever process they run in. The database computes
  it, so the API of the release before this one, the orchestrator and the enterprise edition, which all
  write through `audit.record()` or a plain `INSERT`, are chained without changing a line;
- **the log is append-only**: `UPDATE` and `TRUNCATE` are revoked from the table's owner (the API's own
  role, which runs this migration) and from everyone else, and a `BEFORE UPDATE OR DELETE` trigger
  (`choregos_audit_append_only`) refuses both whatever the privileges say. One `DELETE` passes: the
  organisation's own deletion cascading to its entries (the enterprise edition's end of life, ADR 0024),
  recognised by being fired from the foreign key's trigger once the organisation's row is gone;
- **the history that exists is chained once, here** (the backfill): each chain in the order of `ts` then
  `id`, and a `genesis` checkpoint per chain in `audit_checkpoints` says how many entries the migration
  chained and when. The chain proves those entries did not change after this revision, not before;
- `audit_checkpoints` holds the genesis checkpoints and the signed ones `choregos-admin audit checkpoint`
  writes. It is append-only like the log.

SQLite gets the columns and the tables, never the chain: it keeps no trigger here and is a development
database.

The downgrade drops the triggers, the functions, both tables and the three columns, and gives `UPDATE`
and `TRUNCATE` back to the table's owner: **the chain is lost**, and a later upgrade chains the log again
from scratch, as it stands then.

Revision ID: e2a4c6b8d0f3
Revises: e7b9d1f3a5c8
Create Date: 2026-11-17 23:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "e2a4c6b8d0f3"
down_revision: str | None = "e7b9d1f3a5c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The tables nobody may update or truncate, and whose rows go only with their organisation.
APPEND_ONLY = ("audit_log", "audit_checkpoints")

WHOLE = "'*' = ANY(choregos_current_orgs())"
IN_SCOPE = "choregos_org_slug(org_id) = ANY(choregos_current_orgs())"

#: `sha256(prev_hash || canonical(entry))`, hex. The canonical form is a JSON array rendered by `jsonb`:
#: a version tag, the chain (the organisation's id, or null), the place, the id, the time in UTC to the
#: microsecond, the actor, the action, the target and the payload as stored. ADR 0182 specifies it, and
#: `tranibor_api.audit_chain.canonical` computes the same text from the columns, independently of this
#: function.
HASH_FUNCTION = """
CREATE OR REPLACE FUNCTION choregos_audit_hash(
  prev_hash text, org_id text, seq bigint, id text, ts timestamptz, actor_kind text, actor_id text,
  action text, target_type text, target_id text, payload jsonb
) RETURNS text AS $$
  SELECT encode(sha256(convert_to(prev_hash || jsonb_build_array(
    'choregos.audit/1', org_id, seq, id,
    to_char(ts AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
    actor_kind, actor_id, action, target_type, target_id, payload
  )::text, 'UTF8')), 'hex')
$$ LANGUAGE sql STABLE;
"""

#: The link: lock the chain's head (create it at the first entry), take the next place, hash, move the
#: head. What the writer put in `seq`, `prev_hash` or `hash` is ignored. The head is read under the
#: session's row-level security: its policy lets every session reach the platform's chain, since a session
#: bounded to an organisation writes platform entries it cannot read back (a sign-in).
LINK_FUNCTION = """
CREATE OR REPLACE FUNCTION choregos_audit_link() RETURNS trigger AS $$
DECLARE
  chain_key text := coalesce(NEW.org_id, '');
  last_seq bigint;
  last_hash text;
BEGIN
  SELECT h.seq, h.hash INTO last_seq, last_hash FROM audit_chain_heads h
    WHERE h.chain = chain_key FOR UPDATE;
  IF NOT FOUND THEN
    INSERT INTO audit_chain_heads (chain, org_id, seq, hash)
      VALUES (chain_key, NEW.org_id, 0, repeat('0', 64))
      ON CONFLICT (chain) DO NOTHING;
    SELECT h.seq, h.hash INTO last_seq, last_hash FROM audit_chain_heads h
      WHERE h.chain = chain_key FOR UPDATE;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'audit_log: this session cannot reach the chain of %',
        coalesce(NEW.org_id, 'the platform') USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
  NEW.seq := last_seq + 1;
  NEW.prev_hash := last_hash;
  NEW.hash := choregos_audit_hash(
    NEW.prev_hash, NEW.org_id, NEW.seq, NEW.id, NEW.ts, NEW.actor_kind, NEW.actor_id,
    NEW.action, NEW.target_type, NEW.target_id, NEW.payload
  );
  UPDATE audit_chain_heads SET seq = NEW.seq, hash = NEW.hash WHERE chain = chain_key;
  RETURN NEW;
END
$$ LANGUAGE plpgsql;
"""

#: Append-only. The one `DELETE` let through is a cascade from `organizations` (fired from the foreign
#: key's own trigger, so at depth 2) once the organisation's row is gone: an organisation's deletion takes
#: its chain with it. A `DELETE` sent by a session is at depth 1 and refused, whoever sends it.
APPEND_ONLY_FUNCTION = """
CREATE OR REPLACE FUNCTION choregos_audit_append_only() RETURNS trigger AS $$
BEGIN
  IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 AND OLD.org_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM organizations o WHERE o.id = OLD.org_id) THEN
    RETURN OLD;
  END IF;
  RAISE EXCEPTION '%: the audit log is append-only, % refused (ADR 0182)', TG_TABLE_NAME, TG_OP
    USING ERRCODE = 'insufficient_privilege';
END
$$ LANGUAGE plpgsql;
"""

#: The history as it stands, chained once: each chain in the order of `ts` then `id`, its head written,
#: and a genesis checkpoint for every chain that has entries. The scope is opened first: the migration
#: runs as the API's role, under forced row-level security, and would otherwise see no row.
BACKFILL_AND_GENESIS = """
DO $$
DECLARE
  r record;
  chain_key text;
  current_key text := NULL;
  n bigint := 0;
  prev text := repeat('0', 64);
  h text;
BEGIN
  FOR r IN SELECT * FROM audit_log ORDER BY coalesce(org_id, ''), ts, id LOOP
    chain_key := coalesce(r.org_id, '');
    IF current_key IS DISTINCT FROM chain_key THEN
      IF current_key IS NOT NULL THEN
        INSERT INTO audit_chain_heads (chain, org_id, seq, hash)
          VALUES (current_key, nullif(current_key, ''), n, prev);
      END IF;
      current_key := chain_key;
      n := 0;
      prev := repeat('0', 64);
    END IF;
    n := n + 1;
    h := choregos_audit_hash(
      prev, r.org_id, n, r.id, r.ts, r.actor_kind, r.actor_id, r.action, r.target_type, r.target_id,
      r.payload
    );
    UPDATE audit_log SET seq = n, prev_hash = prev, hash = h WHERE id = r.id;
    prev := h;
  END LOOP;
  IF current_key IS NOT NULL THEN
    INSERT INTO audit_chain_heads (chain, org_id, seq, hash)
      VALUES (current_key, nullif(current_key, ''), n, prev);
  END IF;
  INSERT INTO audit_checkpoints (id, org_id, seq, hash, kind, created_at)
    SELECT gen_random_uuid()::text, org_id, seq, hash, 'genesis', now() FROM audit_chain_heads;
END
$$;
"""


def _postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


#: `UPDATE` and `TRUNCATE` taken from every role that holds them on `TABLE`, its owner included (the
#: owner's default privileges are made explicit, then revoked). The owner may grant them back: that is a
#: `GRANT` the application never sends, and the trigger still refuses an update.
REVOKE = """
DO $$
DECLARE holder text;
BEGIN
  FOR holder IN
    SELECT DISTINCT pg_get_userbyid(a.grantee) FROM pg_class c,
      aclexplode(coalesce(c.relacl, acldefault('r', c.relowner))) a
    WHERE c.oid = 'TABLE'::regclass AND a.privilege_type IN ('UPDATE', 'TRUNCATE') AND a.grantee <> 0
  LOOP
    EXECUTE format('REVOKE UPDATE, TRUNCATE ON TABLE FROM %I', holder);
  END LOOP;
  REVOKE UPDATE, TRUNCATE ON TABLE FROM PUBLIC;
END
$$;
"""

#: The downgrade gives the owner of `TABLE` back what `REVOKE` took from it.
GRANT_BACK = """
DO $$
BEGIN
  EXECUTE format('GRANT UPDATE, TRUNCATE ON TABLE TO %I',
    (SELECT tableowner FROM pg_tables WHERE tablename = 'TABLE' AND schemaname = current_schema()));
END
$$;
"""


def upgrade() -> None:
    with op.batch_alter_table("audit_log") as batch:
        batch.add_column(sa.Column("seq", sa.BigInteger(), nullable=True))
        batch.add_column(sa.Column("prev_hash", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("hash", sa.String(length=64), nullable=True))
    op.create_index("ix_audit_log_chain", "audit_log", ["org_id", "seq"])
    op.create_table(
        "audit_chain_heads",
        sa.Column("chain", sa.String(length=36), nullable=False),
        sa.Column("org_id", sa.String(length=36), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_audit_chain_heads_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("chain", name=op.f("pk_audit_chain_heads")),
    )
    op.create_index(op.f("ix_audit_chain_heads_org_id"), "audit_chain_heads", ["org_id"])
    op.create_table(
        "audit_checkpoints",
        sa.Column("org_id", sa.String(length=36), nullable=True),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("hash", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("key_id", sa.String(length=43), nullable=True),
        sa.Column("signature", sa.String(length=86), nullable=True),
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["org_id"],
            ["organizations.id"],
            name=op.f("fk_audit_checkpoints_org_id_organizations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_checkpoints")),
    )
    op.create_index(op.f("ix_audit_checkpoints_org_id"), "audit_checkpoints", ["org_id"])
    if not _postgres():
        return

    # Row-level security. A head is reachable from a session bounded to its organisation, and the
    # platform's head from every session (it carries a count and a hash, no content). A checkpoint is read
    # like the log and written only by the platform's scope (the CLI).
    for table, read, write in (
        ("audit_chain_heads", f"{WHOLE} OR org_id IS NULL OR {IN_SCOPE}", None),
        ("audit_checkpoints", f"{WHOLE} OR {IN_SCOPE}", WHOLE),
    ):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(f"DROP POLICY IF EXISTS {table}_org_isolation ON {table}")
        check = f" WITH CHECK ({write})" if write else ""
        op.execute(f"CREATE POLICY {table}_org_isolation ON {table} USING ({read}){check}")

    op.execute(HASH_FUNCTION)
    op.execute(LINK_FUNCTION)
    op.execute(APPEND_ONLY_FUNCTION)
    op.execute("SELECT set_config('app.current_orgs', '*', true)")
    op.execute(BACKFILL_AND_GENESIS)
    op.execute(
        "CREATE TRIGGER audit_log_link BEFORE INSERT ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION choregos_audit_link()"
    )
    for table in APPEND_ONLY:
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION choregos_audit_append_only()"
        )
        op.execute(REVOKE.replace("TABLE", table))


def downgrade() -> None:
    if _postgres():
        for table in APPEND_ONLY:
            op.execute(f"DROP TRIGGER IF EXISTS {table}_append_only ON {table}")
            op.execute(GRANT_BACK.replace("TABLE", table))
        op.execute("DROP TRIGGER IF EXISTS audit_log_link ON audit_log")
        op.execute("DROP FUNCTION IF EXISTS choregos_audit_append_only()")
        op.execute("DROP FUNCTION IF EXISTS choregos_audit_link()")
        op.execute(
            "DROP FUNCTION IF EXISTS choregos_audit_hash(text, text, bigint, text, timestamptz, text, text, "
            "text, text, text, jsonb)"
        )
    op.drop_index(op.f("ix_audit_checkpoints_org_id"), table_name="audit_checkpoints")
    op.drop_table("audit_checkpoints")
    op.drop_index(op.f("ix_audit_chain_heads_org_id"), table_name="audit_chain_heads")
    op.drop_table("audit_chain_heads")
    op.drop_index("ix_audit_log_chain", table_name="audit_log")
    with op.batch_alter_table("audit_log") as batch:
        batch.drop_column("hash")
        batch.drop_column("prev_hash")
        batch.drop_column("seq")
