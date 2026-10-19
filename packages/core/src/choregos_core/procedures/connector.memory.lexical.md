---
id: connector.memory.lexical
title: Use the built-in lexical memory
applies_to: {connector: memory/lexical}
audience: operator
version: 1
reviewed: 2026-10-16
egress: []
---

## Before you start

Nothing to install. The lexical memory is the fallback: it searches by words in the platform's own
database, with no external service and no embedding.

## At the vendor

There is no vendor and nothing to create.

## Permissions

It reads and writes only the platform's own tables, under the organisation's isolation.

## In Choregos

Connectors, **Memory**, type **lexical**. There is nothing to configure.

## Test it

Press **Test**, then run a stage and look at its context pack.

## When it fails

- Poor recall on paraphrases: it matches words, not meaning. Use Ecphoria for semantic memory.
