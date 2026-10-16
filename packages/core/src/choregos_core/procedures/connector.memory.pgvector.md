---
id: connector.memory.pgvector
title: pgvector memory (deprecated name)
applies_to: {connector: memory/pgvector}
audience: operator
version: 1
reviewed: 2026-10-16
egress: []
---

## Before you start

This name is **deprecated** and is never offered to a new project. It is kept so projects that already
carry it keep working. It is an alias of the `lexical` memory: the name promised vectors, the code does
full-text search.

## At the vendor

Nothing to create.

## Permissions

Same as the lexical memory: the platform's own tables only.

## In Choregos

Change the project's memory connector to `lexical` (same behaviour) or `ecphoria` (semantic memory).

## Test it

Press **Test** on the new connector and run a stage.

## When it fails

- A project that still names `pgvector` works as `lexical`; if you expected vectors, switch to Ecphoria.
