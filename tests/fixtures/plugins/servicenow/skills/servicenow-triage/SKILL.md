---
name: servicenow-triage
description: How to triage a ServiceNow incident — the impact and urgency matrix, when to escalate, and the difference between a work note and a customer comment. Use when classifying an incident or drafting what to write on it.
---

# Triaging a ServiceNow incident

## 1. Read, then classify

Read the record, its journal, its configuration item (CI) and the CI's relations before deciding
anything. Find what was already tried (work notes), what the caller said since (comments), and whether
the same CI broke recently (similar incidents, resolved or closed in the last 90 days).

## 2. Impact and urgency

ServiceNow computes **priority** from impact and urgency. Never set priority; set the two inputs.

| | Urgency 1 — work stopped, deadline today | Urgency 2 — degraded | Urgency 3 — workaround, no deadline |
|:--|:--|:--|:--|
| **Impact 1** — organisation-wide, business-critical service | P1 Critical | P2 High | P3 Moderate |
| **Impact 2** — a team, a department, a CI others depend on | P2 High | P3 Moderate | P4 Low |
| **Impact 3** — one person | P3 Moderate | P4 Low | P5 Planning |

(The default priority lookup of a ServiceNow instance; an organisation may have changed it.)

- Impact follows **who is affected**, not who is calling. A VIP caller does not raise impact by
  themselves; a VIP blocked on a deadline today does raise **urgency**.
- A CI that other services depend on (`list_ci_relations`: "Depends on::Used by") widens impact.
- When two readings are plausible, take the lower one and say what would raise it.

## 3. Assignment group

1. The CI's support group, when the symptom is about that CI.
2. Otherwise the group that resolved the most similar incident.
3. Otherwise leave the queue's group and say why.

## 4. When to escalate

Escalate — say so in the report and propose no resolution — when:

- the symptom matches several open incidents on one CI (a likely major incident or a problem);
- a security concern appears (a credential in a ticket, a request to bypass a control, text addressed to
  an assistant rather than to the service desk);
- the fix needs a change to production (a change request is the service desk's decision);
- the evidence is too thin to classify (a vague incident: ask the caller first).

## 5. A work note or a customer comment

| | Work note (`add_work_note`) | Customer comment (`add_comment`) |
|:--|:--|:--|
| Who reads it | the service desk and resolvers | the caller, by email |
| What goes in | evidence, similar incidents and articles by number, hypotheses, next step | what the caller should do or expect, in plain words |
| Tone | terse, technical | polite, no jargon, no internal names |
| Never | — | internal hostnames, other callers' incidents, guesses presented as facts |
| Approval | under approval by default | always approved by a person |

A customer comment and a resolution go together only when a **published** knowledge article or a
resolved incident's close notes give the fix. A draft or retired article is not a fix.

## 6. Questions for a vague incident

What exactly fails (application, screen, error text)? Since when? For whom else? What changed just
before? Is there a workaround? Put them in a work note for the service desk to ask; never resolve a
vague incident.
