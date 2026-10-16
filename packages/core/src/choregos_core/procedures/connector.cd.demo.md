---
id: connector.cd.demo
title: The demo deployment (demonstration only)
applies_to: {connector: cd/demo}
audience: everyone
version: 1
reviewed: 2026-10-16
egress: []
fields:
  url: "the endpoint of the one process serving the demonstration fakes; empty to run them in this process"
  api_key: "not needed by the demonstration, typed once if you set one"
---

## Before you start

This is a **demonstration** of the deployment (promotion and health): it is held in memory and is **refused in staging and prod**. Use it
to try the joiners-leavers and delivery templates without any real system.

## At the vendor

There is no vendor. Nothing is created anywhere.

## Permissions

None: nothing leaves the platform. Nothing is stored beyond the process's memory, so it forgets on restart.

## In Choregos

Declare the connector with type `demo`. Leave `url` empty to run the fakes in the same process, or give
the address of the one process that serves them for several replicas.

## Test it

Run the template's workflow. The demonstration answers like the real system would, with invented data.

## When it fails

- A refusal in staging or prod: by design. Declare the real connector instead.
- State that disappears: it lives in memory; with several replicas, set `url` to a single process.
