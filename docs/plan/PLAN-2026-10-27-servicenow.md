# Plan — 2026-10-27: a ServiceNow agent

Source: the product owner's request of 2026-10-27 ("plan an agent for ServiceNow"). Checked against
`main` at `7919257b`. Stream number: **S43**; story ids `S43-NN`. ADR number reserved: **0093**
(0088-0092 are taken by open pull requests).

Status words: **exists** (in the code), **missing**, **broken** (exists and is wrong).

Scope note: this repository is public. Nothing here names a customer's instance; the live proof runs on
a ServiceNow Personal Developer Instance (PDI), which anyone can obtain.

---

## 0. What the audit found before planning

| Question | What the code says | Verdict |
|:--|:--|:--|
| Does Choregos know ServiceNow? | No occurrence anywhere. The only ITSM mention is one conditional line of the bundled `runbook` skill ("If ~~ITSM is connected"). `MemoryKind.INCIDENT` exists | Missing |
| Can a work item come from an external ticketing tool? | Yes: `TrackerAdapter` (`packages/adapters/.../base.py`) with Jira, GitHub, GitLab and the internal tracker; `create_item` (ADR 0059), `parse_webhook`/`verify_webhook`, one route per source in `routers/webhooks.py` | Exists, reusable |
| Can an agent act on an external system under approval? | Yes: an organisation connector declares `OperationSpec`s; the broker exposes them as `<connector>__<operation>` tools; a write becomes a governed action (ADR 0034, 0035, 0052, 0085). `netops` (pfSense) is the closest model | Exists, reusable |
| Can an operation be forced to need a person? | Yes: `OperationSpec.human_approval=True` (ADR 0052), compensation in reverse order | Exists |
| A connector's procedure, egress and secrets? | ADR 0067 (procedure per type, `tests/procedures/test_coverage.py` refuses a type without one), ADR 0066 (egress from `ConnectorTypeSpec.egress`), ADR 0058 (sealed secret fields) | Exists, a new type must ship each |
| A ready-made ITSM workflow? | `templates/joiners-leavers` is the closest shape (manifest, agents, workflows, skills) | Missing |

Conclusion: **no new mechanism is needed.** A ServiceNow agent is a connector type, an agent in the
catalogue and a template, built on the tracker, broker and governed-action machinery that exists.

## 1. Decisions

### Decision 1 — one client, two connector types
ServiceNow plays two roles, and Choregos already has a slot for each:

- **`tracker/servicenow`** (project scope): an incident, a request item or a change is a *work item*. It
  starts a run when it is created or assigned to a chosen group, and the run's status is written back.
  Implements `TrackerAdapter` like `JiraTracker`.
- **`itsm/servicenow`** (organisation scope, new family `itsm` in `familles.py`, a `demo` type served by
  `_guichet_de_demo` like `mdm` or `shipping`): the operations an agent calls through the broker.

Both build on one `ServiceNowClient` (`packages/adapters/.../itsm/servicenow.py`) over the Table API
(`/api/now/table/<table>`), the Aggregate API for counts, and the attachment API read-only. Tables used:
`incident`, `sc_req_item`, `change_request`, `problem`, `kb_knowledge`, `cmdb_ci`, `sys_user_group`,
`sys_journal_field` (read). Every read passes `sysparm_fields` and `sysparm_display_value=all` so the
agent sees labels and the adapter keeps `sys_id`s.

### Decision 2 — the operations of `itsm/servicenow`

| Operation | Access | Policy by default | Notes |
|:--|:--|:--|:--|
| `search_records` (table in a closed list, encoded query built from typed filters, never a raw query string) | read | allowed | caps rows at 50 |
| `get_record` (number or sys_id), `get_journal` (work notes and comments) | read | allowed | |
| `get_ci`, `list_ci_relations` (CMDB) | read | allowed | |
| `search_knowledge` (`kb_knowledge`, published articles only) | read | allowed | |
| `similar_incidents` (same CI or same short-description terms, last 90 days, resolved) | read | allowed | |
| `add_work_note` | write | approval | internal note, idempotent by a marker in the text |
| `add_comment` | write | **human_approval** | customer-visible: never lowered below approval |
| `update_record` (category, subcategory, impact, urgency, assignment group, assignee, CI) | write | approval | compensation restores the previous values read before the write |
| `resolve_incident` (close code, close notes) | write | **human_approval** | compensation reopens |
| `create_change_request` (normal change, from a template) | write | **human_approval** | idempotent by `correlation_id` = action key |

Priority is never written: ServiceNow computes it from impact and urgency. State codes are not
hard-coded: they are read once from `sys_choice` and cached per connector, because instances customise them.

### Decision 3 — authentication
OAuth 2.0 client credentials (an inbound integration registered in the instance) is the default; basic
authentication of a dedicated integration user is accepted for older releases and PDIs. Secret fields:
`client_secret` or `password`, and `webhook_secret`. The procedure asks for a **dedicated account with a
custom role** (read on the tables above, write on `incident.work_notes`, `incident.comments` and the
fields of `update_record` only), not `admin`, not a broad `itil` grant when a narrower role will do.
Egress: `<instance>.service-now.com` (or the custom host in `base_url`).

### Decision 4 — inbound events
ServiceNow has no generic outbound webhook. The procedure ships a **Business Rule** (after insert/update on
`incident`, filtered on the assignment groups the project listens to) that calls an outbound REST message
to `{{webhook_url:servicenow}}` with the `X-Choregos-Secret` header, payload `{table, sys_id, number,
sys_mod_count, operation}`. The route `POST /webhooks/servicenow` verifies the secret, de-duplicates on
`sys_id` + `sys_mod_count`, and **re-reads the record through the API** rather than trusting the payload.
A project without the Business Rule still works: `list_candidates` polls the assignment queue.

### Decision 5 — the agent: `itsm-triager`
A catalogue entry (`catalogue_d_agents/agents/itsm-triager.yaml`) modelled on `netops`: it reads, explains
and proposes; every write is a governed action.

1. Read the incident, its journal, its CI and the CI's relations.
2. Search similar resolved incidents and published knowledge articles; quote them by number.
3. Propose category, impact, urgency and assignment group, each with its evidence.
4. Draft a work note (diagnosis and next steps) and, when the incident is a known problem with a published
   fix, a customer-visible comment and a resolution — both waiting for a person.
5. Output `{"itsm_triage_markdown": "…"}` with sections `## Findings`, `## Proposed classification`,
   `## Proposed actions`, `## Confidence`. When the evidence is thin, it says so and proposes only a work note.

Tools: `{ connector: servicenow, tools: ["search_*", "get_*", "list_*", "similar_incidents",
"add_work_note", "update_record", "add_comment", "resolve_incident"] }`; limits 30 turns, 15 minutes, a
small run budget. Eval cases (`eval_cases/itsm-triager/`) on a seeded fake: a duplicate of a known incident,
a vague one with no evidence (must not resolve), a VIP caller (must not comment without approval), an
injected instruction inside the description (must not obey it — ADR 0084).

A second agent, **`change-author`**, is phase 2: it drafts a normal change request from a delivery run
(diff, risk, rollback, test evidence) for organisations whose production changes go through a CAB.

### Decision 6 — the template: `itsm-incident-triage`
`templates/itsm-incident-triage/`: manifest requiring `tracker: servicenow` and an org connector of
capability `itsm`; one workflow `incident → triage (agent) → review (human, service desk group) →
apply (system: the approved actions) → done`, with `on_fail` back to the queue untouched. Skills: the
existing `runbook` skill, whose "~~ITSM" line becomes true. Phase 2 adds a gate to
`software-delivery-governed`: "a change request is approved" before the production stage.

### Decision 7 — where it ships
Community edition (ADR 0024: connectors and their fakes stay in it). Nothing goes to the enterprise edition.

## 2. Stories and order

| Story | Title | Depends on |
|:--|:--|:--|
| S43-01 | `contract(openapi)`: `POST /webhooks/servicenow`; `itsm` family in the connector catalogue | — |
| S43-02 | `ServiceNowClient` + `FakeServiceNow` (seeded tables, `sys_choice`, journal, CMDB) | — |
| S43-03 | `tracker/servicenow`: `TrackerAdapter`, state mapping from `sys_choice`, `list_candidates` | 02 |
| S43-04 | `itsm/servicenow`: the operations of Decision 2, compensations, conformance tests | 02 |
| S43-05 | Webhook route, de-duplication, re-read; replayed history for the triggered workflow | 01, 03 |
| S43-06 | Procedures `connector.tracker.servicenow.md` and `connector.itsm.servicenow.md` (OAuth app, custom role, Business Rule script); ADR 0093 | 03, 04, 05 |
| S43-07 | Catalogue agent `itsm-triager` + eval cases | 04 |
| S43-08 | Template `itsm-incident-triage` + `apps/orchestrator/tests/test_templates_livres.py` | 05, 07 |
| S43-09 | Live tests (`tests/live/servicenow`, skipped without `CHOREGOS_LIVE_SERVICENOW_*`) against a PDI | 03, 04 |
| S43-10 | Phase 2: `change-author` agent and the change-approved gate in `software-delivery-governed` | 08 |

```
S43-02 ─► 03 ─► 05 ─► 06 ─► 08 ─► 10
     └──► 04 ─► 07 ──────────┘
S43-01 ─► 05            S43-09 after 03 and 04
```

Acceptance shared by every story: `make ci` green; a STATUS row saying what the PR proves and does not
prove; each guard (human approval on `add_comment`/`resolve_incident`, closed table list, no raw encoded
query, de-duplication, re-read) has a test that fails without it.

## 3. ADR taken in this stream

| ADR | Title | Amends |
|:--|:--|:--|
| 0093 | ServiceNow is a tracker and an `itsm` connector over one client; customer-visible writes always need a person | 0034 (connector types), 0067 (two procedures) |

## 4. What stays with the operator

- A ServiceNow PDI (or a sub-production instance) for S43-09, its OAuth inbound integration, and the
  integration user with the custom role; their values in the operator's secret store.
- The Business Rule and outbound REST message in the instance (the procedure gives the script).
- The egress rule for the instance host on the operator's platform.
- Merges into `main` and the release that carries the stream.
- Phase 2 needs an organisation that really runs a CAB to say which change template and which approval
  states count as "approved".
