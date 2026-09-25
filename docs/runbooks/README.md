# Runbooks

A runbook is read at 3 a.m. by someone who did not write the code. Each one starts with
*how to know it is this*, then gives the commands, then says *how to check it is fixed*.

English is the reference language since 2026-09-24; the French originals are kept in
[`docs/fr/runbooks/`](../fr/runbooks/) and no longer maintained. The files keep their
original names — alerts and other pages point at them.

| Runbook | When |
| :-- | :-- |
| `train-gele.md` | a train is frozen and nothing departs |
| `api-5xx.md` | the API returns 5xx |
| `restauration-postgres.md` | data loss, PITR restore — the only one played on a cluster so far |
| `ecphoria-quorum.md` | Ecphoria lost its Raft quorum |
| `rotation-secrets.md` | rotating the GitHub App, the gateway `master_key`, the JWT keys |
| `montee-temporal.md` | upgrading Temporal |
| `purge-runs.md` | the database grows, transcripts pile up |
| `run-bloque.md` | a run stays `Pending` or never ends |
| `webhook-perdu.md` | an `agent-ready` ticket did not start |
| `perte-de-noeud.md` | a node disappears (spot eviction, failure) and work does not resume |
| `aca-live.md` | checking the Azure Container Apps executor against a real subscription |
