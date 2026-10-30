# 0113 — A workflow starts from a trigger: tracker, HTTP, schedule or a person

- **Status**: proposed, 2026-10-29 — S51-01 implements the HTTP trigger and the common audit; S51-02 to
  S51-05 the console, start rules, signed deliveries and the `when` filter (§8-11)
- **Concerns**: `ProjectConfig.triggers` (contract), `routers/triggers.py`, `services/triggers.py`,
  `services/received_events.py`, `activities/schedules.py`, the `trigger_tokens` table; follows
  [ADR 0031](0031-several-workflows-per-project.md) (a project's workflows and their routing) and S42-06f
  (the received events)

## Context

The product owner asked on 2026-10-29: "I had planned an audit system for the events that enter Choregos;
check it is there. I want to go further: a specific workflow, or one of a project's workflows chosen by a
routing, must start from a trigger — external (ServiceNow, HTTP), cron or manual."

What the code held that day:

| Door | What starts | Which workflow | Audited |
|:--|:--|:--|:--|
| Tracker webhook (GitHub, GitLab, Jira, a plugin tracker such as ServiceNow, S43-07) | an item labelled `agent-ready` | routing by labels and item type, else the default | yes, `received_events` (S42-06f) |
| Schedule (`schedules:`, S38-11) | one work item per cron window | the schedule's `workflow`, else routing (label `schedule:<name>`) | no (a log line) |
| A person (console, REST, MCP door, A2A door) | a work item | the one chosen, else routing | the audit log (`workitem.create`) |
| Any other system over HTTP | — | — | — |

Every door already goes through the one birth path, `services/routage.nouveau_ticket`: an explicit workflow,
else the first routing rule that matches the labels, else the default. What was missing is a door for a
system that is not a tracker — a monitoring tool, a form, a CRM, a ServiceNow Flow that only needs to
*start* something — and a single place that answers "what came in, and what did it start?".

## Decision

1. **A trigger is one more door to the same birth path.** Choosing the workflow stays where it is: an
   explicit `workflow` on the trigger wins, else the project's routing reads the work item's labels, else
   the project's default. There is no second routing engine; a trigger only decides which labels the work
   item carries.
2. **The HTTP trigger is declared in the project's configuration**, like a schedule:

   ```yaml
   triggers:
     - name: incident
       title: "{/number} — {/short_description}"
       body: "Caller: {/caller/name}"
       key: /sys_id
       labels: ["{/category}"]
       fields: { number: /number, ci: /cmdb_ci }
       workflow: incident-triage      # optional: the routing otherwise
   ```

   The mapping is JSON pointers (RFC 6901): a field keeps the value's JSON type and is validated by the
   workflow's `metadata.inputs`; text takes `{/pointer}` placeholders. No expression language: a payload
   cannot make the platform evaluate anything. The work item's labels are `trigger:<name>` followed by the
   rendered ones, so a routing rule can match the trigger, the payload, or both.
3. **It is called at `POST /webhooks/triggers/{org}/{project}/{name}` with its own token** —
   `Authorization: Bearer`, `X-Choregos-Token`, or `?token=` for a sender that can only set a URL. The
   token is minted by `POST /projects/{id}/triggers/{name}/token` (`connector:write`), shown once, and only
   its SHA-256 is kept (`trigger_tokens`, under forced RLS); minting again revokes the previous one. A
   trigger without a token does not answer (404). The deployment's shared webhook secret is not reused:
   one leaked token opens one door of one project.
4. **Twice is once.** A delivery is de-duplicated by `Idempotency-Key`, else by the body's digest. A
   trigger with a `key` opens one work item per value (`<PREFIX>-<trigger>.<value>`): the next call for the
   same incident answers `already_open` and starts nothing. A refusal is judged inside a savepoint and
   keeps no dedup row, so the corrected call goes through.
5. **A person fires the same trigger by hand** (`POST /projects/{id}/triggers/{name}/fire`, `item:control`):
   the same mapping and routing, initiated by that person — the way to test a trigger before giving its URL
   away, and the "manual" trigger of the request. Starting a work item in a chosen workflow from the
   console, the REST API or MCP is unchanged.
6. **One audit for every door without a person in front of it.** `received_events` now also holds each
   trigger call (`trigger:<name>`, `trigger.http` or `trigger.manual`) and each schedule firing
   (`schedule:<name>`, `schedule.fired`), with two new outcomes: `already_open` and `refused`. A refused
   call is recorded — it is what "why did nothing start?" needs — and its summary keeps the platform's
   own words (workflow, field paths), never a value from the payload. A refused schedule window is
   recorded once, not at every pass of its catch-up hour.
7. **The internal tracker only, as schedules.** A project whose tracker is external refuses its triggers
   (409, recorded): creating the issue in Jira or GitHub inside a webhook request cannot be undone if the
   transaction then fails, and that tracker already has its own door.

8. **A trigger can filter** (S51-05): `when` maps a JSON pointer to the text, or texts, the payload must
   hold. A payload that does not match is acknowledged (202) and recorded `ignored` — it is not an error
   of the sender, so it is not `refused`, and its dedup row is kept.
9. **A trigger can take a signature instead of the token** (S51-04): with `auth: signature`, the caller
   sends `X-Choregos-Signature: sha256=<HMAC-SHA256 of the body>`, hex or base64, and the bearer token is
   refused. The key is the signing secret shown with the token: an HMAC of the token's digest under the
   deployment's session secret, so nothing more is stored and it changes with the token (and with the
   session secret).
10. **A tracker item can start on a project rule** (S51-03): `start_rules:` lists conditions on the item's
    labels (`labels_any`, `labels_all`), type and state; when a tracker event (created, updated, labelled
    or moved) names an item the project does not hold yet, the first rule whose every condition holds
    starts it as `agent-ready` does. A rule without a condition is refused. Without rules, nothing changes.
11. **The console shows the triggers** (S51-02), beside the received events: the URL, the workflow or the
    routing, whether a token exists, minting it (shown once, with the signing secret of a signed
    trigger), and firing it with a payload typed by hand.

## ServiceNow

Two ways in, chosen by what the project needs:

- **Two-way**, the work item *is* the incident: the ServiceNow plugin as the project's tracker (S43-07). Its
  Business Rule posts to `/webhooks/plugins/{org}/servicenow/servicenow`; work notes and state go back.
  An incident starts when it matches a start rule (`item_type: incident`, `states: [New]`), or once
  labelled `agent-ready`.
- **One-way**, ServiceNow asks for work and does not need the work item mirrored: an HTTP trigger. A
  Business Rule or a Flow Designer REST step posts the incident to the trigger URL with the token in
  `Authorization`; `key: /sys_id` keeps one work item per incident.

## Consequences

- A project can be started by any system that can send JSON, without a plugin and without a person's API
  token.
- The received events page answers for webhooks, triggers and schedules alike; the console labels the two
  new outcomes.
- Start rules are read on webhook deliveries and by the backup poll (`list_candidates`): Jira turns each
  rule into a JQL clause ORed with `agent-ready`; GitHub and GitLab search each set of labels a rule
  names (a rule on a type or a state is left to the webhook, their issue search cannot express it); a
  plugin tracker's server returns its own candidates.
- The console edits `triggers` and `start_rules` as JSON in the triggers card; the API validates them.
