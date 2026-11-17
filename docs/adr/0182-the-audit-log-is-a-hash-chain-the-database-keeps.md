# 0182 — The audit log is a hash chain per organisation that the database keeps, and refuses to change

- **Status**: accepted, 2026-11-18 (decided by the product owner after review of the pull request;
  proposed 2026-11-17, S80-05, an irreversible migration of the audit log)
- **Concerns**: `audit_log`, `audit_chain_heads` and `audit_checkpoints` (migration `e2a4c6b8d0f3`,
  `apps/api/src/choregos_api/migrations/versions/20261117_2300_audit_chain.py`), `choregos_api.audit_chain`,
  `choregos-admin audit verify|checkpoint` (`admin_cli.py`); leaves `choregos_api.audit.record` unchanged;
  touches the enterprise edition's end of life (`DELETE /orgs/{org}`, ADR 0024) through the foreign key only;
  builds on [ADR 0148](0148-the-outpost-door-is-a-third-door-with-its-own-contract.md) §13 for the key
  format and its rotation

## Context

The product's promise is that a governed platform can show what happened. Until this decision the audit
log could not. `audit.record()` adds a row and nothing else: the API's own role could `UPDATE`, `DELETE`
or `TRUNCATE` `audit_log`, and no reader could tell a row that had been changed from one that had not. The
review of 2026-11-16 put it among the critical findings ("the audit log is not evidence: a plain insert,
no chaining, no append-only table, no SIEM export").

What any answer has to live with, read from the code on 2026-11-17:

- **Who writes.** Every core writer goes through `audit.record()` (about ninety modules of the API; the
  orchestrator through the API's services). The enterprise edition imports the same function, and deletes
  an organisation with `session.delete(organisation)`, its entries going with it by the foreign key
  (`audit_log.org_id … ON DELETE CASCADE`), then writes a platform entry `org.delete`. During an upgrade,
  the pods of the release before keep writing while the migration Job runs (expand and contract, N-1).
- **Row-level security.** `audit_log` is under forced RLS (migration `c3e5a7f9b1d4`). A platform entry
  (`org_id IS NULL`: a sign-in, a token) is written by sessions bounded to an organisation, which cannot
  read it back. The migration Job connects as the API's own role, which therefore **owns** the tables.
- **SQLite** is a development database the API lays with `create_all()`; no migration runs there.
- A new API path is a contract change (ADR 0001); the verifier must not need one.

## Decision

1. **One chain per organisation, and one for the platform's entries.** An organisation's chain holds its
   entries and nothing else, so it can be verified, exported and deleted with the organisation without
   reading or breaking anyone else's. The platform's entries (`org_id IS NULL`) form the platform's chain.
   A single chain for the instance was discarded: verifying one organisation would read every other one's
   entries, and deleting an organisation would cut it.

2. **An entry's canonical form, version 1.** The text `jsonb` renders for the JSON array

   `["choregos.audit/1", org_id, seq, id, ts, actor_kind, actor_id, action, target_type, target_id, payload]`

   where `org_id` is the organisation's id or `null` (an id, not a slug: slugs are renamed, ADR 0090),
   `seq` the entry's place in its chain (1, 2, …, a JSON number), `ts` the time in UTC to the microsecond
   as `YYYY-MM-DDTHH:MM:SS.ffffffZ`, absent values `null`, and `payload` the stored `jsonb` itself. That
   is: elements separated by `", "`, strings escaped as JSON escapes them with non-ASCII characters left as
   they are (`"`, `\`, `\b`, `\f`, `\n`, `\r`, `\t`, and `\u00xx` for the other control characters), and
   the payload exactly as PostgreSQL renders the stored value (`payload::text`: keys ordered shortest
   first, numbers written out in full). A verifier takes that rendering verbatim and never parses it back:
   a number does not survive the round trip through every language's floating point. Any other form takes
   another version tag; the tag never changes meaning.

3. **The link.** `hash = sha256(prev_hash || canonical(entry))`, lower-case hex over the UTF-8 bytes,
   `prev_hash` being the previous entry's `hash` (64 hex characters, so the concatenation needs no
   separator) and 64 zeros for the first entry. Each row carries `seq`, `prev_hash` and `hash`.

4. **The database links the entry, in a trigger, under a lock on the chain's head.** `BEFORE INSERT ON
   audit_log`, `choregos_audit_link()` locks the chain's row in `audit_chain_heads` (`SELECT … FOR UPDATE`,
   creating it at the chain's first entry), takes the next place, hashes the entry with
   `choregos_audit_hash()`, and moves the head. Whatever the writer put in the three columns is
   overwritten. Two writers of one chain are therefore put in a line by the row lock, held from the insert
   to the end of the writer's transaction, whichever process, replica or release they run in.
   - *Why the database and not `audit.record()`*: every writer is chained without changing a line — the
     pods of the previous release during the upgrade, the orchestrator, the enterprise edition, a plain
     `INSERT`. And the code would have needed the same lock and the same read of the head, which a
     session bounded to an organisation cannot do for the platform's chain under RLS.
   - *Why a head row and not an advisory lock and a read of the last entry*: the last platform entry is
     invisible to a bounded session, the head row is not (its policy: the platform's head is reachable from
     every session, an organisation's from a session that holds the organisation; it carries a count and a
     hash, no content). The head is a cache of the chain's end, never evidence: the verifier walks the
     entries and compares their end with it.
   - *Why not a sequence and a sealer that chains afterwards*: the sealer would need `UPDATE` on the log,
     which point 5 takes away, and every entry would wait unchained for it.
   - *The cost*, said plainly: writers of one chain wait for each other from their insert to their commit.
     The application's sessions do not flush before the commit (`autoflush=False`), so the insert usually
     happens at the commit; a transaction that flushes an entry early and then works for long holds its
     chain for as long. A transaction that writes to two chains takes two locks, and two transactions that
     take them in opposite orders deadlock; PostgreSQL aborts one of them, which then fails as any
     deadlock does. No load test measures this yet.

5. **Append-only, enforced by the database twice.** The migration revokes `UPDATE` and `TRUNCATE` on
   `audit_log` and `audit_checkpoints` from every role that holds them, the owner (the API's role)
   included, and installs `BEFORE UPDATE OR DELETE` triggers (`choregos_audit_append_only()`) that refuse
   both whatever the privileges say. `DELETE` stays granted, because the foreign key's cascade runs with
   the owner's rights; the trigger lets exactly that through: a `DELETE` fired from another trigger (the
   foreign key's, depth 2) for an entry whose organisation's row is already gone. An organisation's
   deletion therefore takes its chain with it, and nothing else deletes an entry. The depth condition is a
   second lock behind the organisation's absence, which the foreign key already makes sufficient.

   This is **tamper-evident, not tamper-proof**, and the limit is named: the API's role owns the tables,
   so with DDL it can grant itself `UPDATE` again or drop a trigger; a superuser can do anything. The
   application sends neither, and the chain shows what such a hand changes, as long as a checkpoint was
   kept where that hand does not reach (point 7). Giving the migration its own owner role, distinct from
   the role the API connects as, would turn the revocation into a wall for the API; that is a deployment
   change for a later story.

6. **The history that exists is chained once, by the migration.** Each chain in the order of `ts` then
   `id`, and one **genesis** checkpoint per chain that has entries (`audit_checkpoints.kind = genesis`,
   unsigned) records how many entries the migration chained and when. The chain proves that those entries
   did not change **after** the migration, not before; the verifier says so for every chain that has a
   genesis.

7. **Signed checkpoints, with a key the database does not hold.** `choregos-admin audit checkpoint`
   verifies each chain and, if it is intact, signs its last entry: Ed25519 over the compact JSON (sorted
   keys) `{"v": "choregos.audit.checkpoint/1", "chain": <org id, or "" for the platform>, "seq", "hash",
   "at"}`. The key is the outposts' key format (ADR 0148 §13: a PEM PKCS#8 private key, a public key
   written as the unpadded base64url of its 32 bytes, `config.ed25519_public_key`), but **a key of its
   own**, never the door's. It is read once, from a file or from standard input
   (`--key-file -`), so that it lives where the operator keeps it and **not** in the API's pods or their
   environment: a key the API holds would be held by whoever takes the API. The checkpoint is kept in
   `audit_checkpoints` (written by the platform's scope only, read like the log) and printed as one JSON
   line, to be kept **outside** the database: an anchor.

   *Rotation*: generate a new key and sign with it from then on. Each checkpoint records its key's public
   half (`key_id`), so older ones keep verifying; the verifier is told which public keys to trust
   (`--trust`, repeated: the current one and the former ones), and a checkpoint signed by a key it is not
   told to trust fails the verdict. A key believed compromised is removed from the trust list, and a new
   checkpoint is signed with its successor once the chain verifies.

8. **The verifier is a command, not an API path.** `choregos-admin audit verify [--org SLUG]…
   [--no-platform] [--trust KEY]… [--anchor FILE]… [--json]` walks each chain in the order of `seq`,
   recomputes every hash from the columns with its own implementation of point 2 (it does not call the
   database's function), and reports, per chain, the first break: an entry outside the chain (inserted with
   the trigger off), a place missing (deleted), a place taken twice (a fork), an entry that does not link
   to the one before (rewritten), an entry whose content no longer gives its hash (altered), an entry a
   checkpoint vouched for that is no longer the one it vouched for, and an end shorter than the head or
   than a checkpoint (deleted from the end). Exit code 1 for a broken chain, 2 when there is no chain to
   read (SQLite). A rewrite of everything from some entry on, hashes recomputed and head moved, walks
   clean: only a checkpoint the rewriter could not reach shows it, which is what point 7 is for.

9. **Not decided here, each its own story.** Exporting the log to a SIEM; retention (an append-only log
   that must also forget needs its own rule: dropping a prefix behind a signed checkpoint, say); signing
   checkpoints on a schedule (a CronJob that alone mounts the key); showing `seq` and `hash` in `GET
   /audit` (a contract change); a migration owner distinct from the API's role (point 5); the enterprise
   edition writing the deleted organisation's last `seq` and `hash` into its `org.delete` entry, so that the
   platform's chain remembers the end of the chain that went.

## Consequences

- Every entry written on PostgreSQL from this release on is linked, by whatever wrote it; the API's own
  role cannot update or truncate the log, and cannot delete an entry of an organisation that exists.
- `audit.record()` and its callers do not change, nor does `GET /audit`. The enterprise edition's export
  of an organisation (`select(AuditLog)`) carries the three new columns, so an exported organisation's
  chain can be checked offline from its export.
- The migration locks `audit_log` for its whole transaction (adding the columns) and updates each existing
  row once: the API's writes of audit entries wait for it. On a large log, a `VACUUM (ANALYZE) audit_log`
  afterwards reclaims the rows the backfill replaced. Its downgrade drops the chain, the triggers and the
  two tables and gives `UPDATE` and `TRUNCATE` back to the owner; a later upgrade chains the log again
  from scratch, as it stands then.
- A test harness that empties the tables as the superuser (`TRUNCATE`) still works; one that does it as
  the API's role is refused, which is the point.
- These are the first triggers of the core's schema.

## Alternatives discarded

- **Hashing in `audit.record()`.** Leaves out every writer that does not call it, needs the same
  serialisation, and cannot read the platform chain's end from a bounded session.
- **One chain for the whole instance.** Couples organisations that RLS keeps apart, and an organisation's
  deletion would cut it (point 1).
- **A sequence plus a background sealer.** Needs `UPDATE` on the log and leaves a window of unchained
  entries (point 4).
- **An external ledger (a transparency log, a ledger database).** A second system to run, back up and
  secure for every deployment, before a single customer asked for it; the signed checkpoints and their
  anchors can be shipped to one later without changing the chain.
- **Signing every entry.** A private key in every writer, the API's pods included, which is where it
  protects the least; a signed checkpoint covers every entry before it.
