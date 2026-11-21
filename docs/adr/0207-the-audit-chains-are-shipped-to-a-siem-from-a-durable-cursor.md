# 0207 — The audit chains are shipped to a SIEM from a durable cursor, by a loop of the API

- **Status**: proposed, 2026-11-20 (S80-05b, the SIEM export; not yet reviewed by the product owner)
- **Concerns**: `tranibor_api.audit_export` (`events`, `sinks`, `exporter`), `audit_chain.links_after`, the
  table `audit_export_cursors` (migration `a9c1e3b5d7f0`), the settings `TRANIBOR_AUDIT_EXPORT_*`, the
  chart's `global.auditExport`, three metrics; builds on [ADR 0182](0182-the-audit-log-is-a-hash-chain-the-database-keeps.md)
  point 9 and takes only its first item: retention, scheduled checkpoints, the migration role and the chain
  in `GET /audit` stay open

## Context

ADR 0182 made the audit log tamper-evident and named the limit: the API's role owns the tables, and only a
checkpoint kept where that hand does not reach shows a rewrite of the whole chain. A SIEM is the usual such
place, and the review of 2026-11-16 listed the missing export among its critical findings. What the decision
has to live with, read from the code on 2026-11-20:

- **The chain is per organisation, plus the platform's.** Entries of one chain commit in the order of `seq`:
  the trigger holds the chain's head row until the writer's transaction ends, so no `seq` becomes visible
  after a greater one. A reader that remembers the last `seq` it shipped cannot skip an entry that commits
  later.
- **The platform's periodic jobs are loops of the API**, started in the lifespan and run by every replica
  (`relay_loop`, `push_loop`, `retention_loop`); `push.py` is already an at-least-once delivery with a
  cursor. The orchestrator is Temporal's worker; its activities do not read the audit tables.
- **The audit tables are under forced RLS** and read as the platform (`*`).
- **Receivers differ** in what they accept (a JSON body, NDJSON, syslog frames) and in what they can
  deduplicate; none of them gives the platform a transaction.

## Decision

1. **One loop in every API replica; the cursor's row lock makes one replica ship a chain at a time.**
   `audit_export.exporter.export_loop` is started in the lifespan when `TRANIBOR_AUDIT_EXPORT` is set (off by
   default, PostgreSQL only). Per chain it opens a transaction, locks the chain's cursor
   (`SELECT … FOR UPDATE SKIP LOCKED`: a replica that finds it locked skips the chain), reads the next batch
   after `seq`, calls the sink, and moves the cursor in the same transaction. A Temporal schedule was
   discarded: it would need a database-reading activity in a process that has none, a second connection
   path to the audit tables, and an election the row lock gives for free. A leader election by advisory
   lock was discarded for the same reason: the lock on the cursor is the election, per chain.

2. **The guarantee is: in order per chain, at least once, never ahead of the sink.** The cursor
   (`audit_export_cursors`: `sink`, `chain`, `seq`, `hash`, `updated_at`) moves only after `send` returned,
   inside the transaction that holds its lock. An error or a crash before the commit rolls the move back
   and the batch is read and sent again. The loop is replayable in the sense of ADR 0008: running a pass
   twice has the effect of running it once at the receiver that can deduplicate, and a repeat is
   recognisable at the one that cannot. Not exactly once: a sink that stored a batch and lost its answer
   receives it twice.

3. **An event carries what verifies the chain.** `seq`, `prev_hash`, `hash`, the entry's fields, its
   organisation (id and slug) and, unless switched off (`TRANIBOR_AUDIT_EXPORT_CANONICAL`), `canonical`: the
   text of ADR 0182 point 2 that the hash covers, read with `CAST(payload AS TEXT)` (which renders as
   `payload::text` does). A receiver checks `sha256(prev_hash + canonical) == hash` and that `prev_hash` is
   the hash it saw at `seq - 1`, from the event alone and in any language; it needs no platform code and
   never re-renders a payload (a number does not survive every language's floating point). The event's
   shape is tagged `choregos.audit.export/1`; a new shape gets a new tag.

4. **The cursor is not evidence and the export does not verify.** A lost or deleted cursor ships the chain
   again from entry 1; a cursor that does not meet the chain (a prefix removed, a rewrite) is logged
   (`audit.export.chain_discontinuity`) and what follows is shipped with its own `prev_hash`, which tells the
   receiver. Whether the chain is intact stays `choregos-admin audit verify`'s, and the receiver's.

5. **Two kinds of sink, a fake, one per deployment.** `https`: one POST per batch, a token in a configurable
   header (`Authorization: Bearer` by default; `Splunk` for HEC, `ApiKey` for Elastic; a bare token for any
   other), over `https://` only, with no redirect followed, bodies for Splunk HEC (concatenated `{"time",
   "event"}` objects), Elasticsearch and OpenSearch `_bulk` (documents named `<chain>:<seq>`, so a repeat
   overwrites) and a generic webhook (`{"source", "events"}`); a 2xx that carries a refusal (Splunk's `code`,
   Elastic's `errors`) is a failure. `syslog`: RFC 5424 over TLS with RFC 5425 octet counting, the chain in
   structured data and the event as the message, or in CEF; a connection per batch, the server's certificate
   verified against the system's roots or `caFile`. `fake`: in memory, scriptable, accepted only under
   `TRANIBOR_FAKES=1`. The sink is named (`TRANIBOR_AUDIT_EXPORT_NAME`, default `siem`): the name is the
   cursor's key, so another name ships everything again. A second sink at once is not built; the cursor's
   key already allows it.

6. **Syslog gives no acknowledgement, and the decision says so.** TCP and TLS tell the sender a byte was
   written, not that the collector kept it. A syslog batch is "accepted" when it was written and flushed;
   one in flight when the connection breaks can be lost, and the receiver sees the missing `seq` in the
   chain. A deployment that cannot accept that uses an HTTPS sink whose answer is the receiver's verdict.

7. **Scope: every chain of the deployment, an optional filter by organisation.** The default exports every
   organisation and the platform's chain (`TRANIBOR_AUDIT_EXPORT_ORGS`, a JSON list of slugs, and
   `TRANIBOR_AUDIT_EXPORT_PLATFORM`). The export is the deployment's, configured by its operator in the
   chart, with the token from an existing Secret (`valueFrom.secretKeyRef`); an organisation does not
   configure its own sink here. An organisation's own export (its sink, its token, in the console) is a
   later decision: it needs a place to keep a tenant's secret, and the review of what an organisation may
   open at the network.

8. **What the operator can see.** Three series on `/metrics`, computed from the database like the others and
   so the same on every replica: `choregos_audit_export_lag_entries{sink}` (the chains' heads minus the
   cursors) and `choregos_audit_export_progress_timestamp_seconds{sink}`; and, per process,
   `choregos_audit_export_events_total` and `choregos_audit_export_failures_total`. Logs name each failed
   chain with the sink's own error, never the token. A failing receiver stretches the wait between passes
   (doubling, to five minutes). A setting that cannot build a sink stops the API at start.

9. **A new table, under forced RLS, the platform's alone.** `audit_export_cursors` (migration
   `a9c1e3b5d7f0`) is read and written under the platform's scope only; it cascades with its organisation.
   The chart renders the settings and an egress policy for the API, and nothing when the export is off.

## Consequences

- With the export on, the SIEM holds a copy of the log that whoever holds the database cannot rewrite, up to
  what the sink has received. It narrows the window ADR 0182 named; it does not close it, and a SIEM's
  own retention and access are the operator's.
- A deployment with a large unexported log ships it in batches of `TRANIBOR_AUDIT_EXPORT_BATCH` (500) at one
  batch per chain per pass, and then each pass until the chain is caught up: the first export is as long as the
  log. The transaction that holds a cursor stays open during the sink's call, so a slow sink holds only its
  own chain's cursor.
- Writers are not affected: the export reads, and takes no lock on the log or on a head.
- Retention, when it comes, must keep a cursor meaningful (a removed prefix is behind every cursor that
  matters, by design) and the discontinuity log is its tripwire.

## Not decided here

An organisation's own sink; shipping a verification verdict or the signed checkpoints to the SIEM; a second
sink at once; mutual TLS to the receiver (a client certificate) and an HTTP proxy; dead-lettering a batch a
receiver always refuses (today it is retried for ever, with the backoff, and the lag shows it); retention,
checkpoints on a schedule, the migration role and the chain in `GET /audit` (ADR 0182 point 9).
