# 0157 — The core names no operator

- **Status**: accepted, 2026-11-12; S69-02 implements it
- **Concerns**: every tracked file outside history; `tests/licences/test_no_operator_in_core.py`

## Context

Choregos is installed and run by operators: a company that hosts an instance for its own teams, a
service provider that runs one for its customers. The first instances in service were one operator's,
and what that operator needed was written into the core as if it were the platform's: its name in the
comments of the charts and of the executors, its hosts in a chart test, its GitHub organisation in the
console's demonstration data, its Jira site in a fixture, its tenants as the reason for a default in the
documentation. 85 tracked files named it.

A reader outside that operator learns from these lines that the core is somebody's, and a default that
reads "on that platform" invites the next one to be written for that platform too. ADR 0079 already
keeps another product out of the core's code; nothing kept an operator out.

## Decision

1. **The core names no operator.** A comment, a test, a fixture, a chart, the demonstration data or a
   page of documentation that needs an example uses a neutral one: `example.com`, `acme`, `globex`. A
   lesson learnt on an instance keeps its date and its facts and says "an operator's dev tenant", not
   whose.
2. **History keeps what happened where.** The ADRs, `docs/plan/` and the trial records of `trials/` may
   name the instance they were measured on; they are not rewritten.
3. **The legal notices name the companies they are about.** `NOTICE` and `docs/TRADEMARK.md` change only
   with the notices themselves, never as an example; when they stop naming an operator, the exception
   goes.
4. **A test refuses it.** `tests/licences/test_no_operator_in_core.py` reads every tracked file for the
   operator's names in any case (its company name in any form: a host, a GitHub organisation, a plugin;
   and the Jira site a fixture was mirrored from) and allows only history, the two notices and itself.
   An exception that no longer names an operator is refused, so the list only shrinks.

## Consequences

- A chart default, a policy or a template that one operator relies on is its deployment's value, set in
  its own repository, not the core's default. Nothing of the core's rendered output changed with this
  decision: every mention removed was a comment, a test value or demonstration data.
- An operator's own vocabulary that is not its name (a Jira project key, a workflow or agent name used
  as test data) is not refused by the test; a story that meets one in shipped code rewords it.
