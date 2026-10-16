# 0067 — A procedure per connector type and per external agent type

- **Status**: accepted, 2026-10-16
- **Concerns**: `choregos_core.procedures` (the files and the loader), the API (`GET /procedures`),
  the contract (`openapi.yaml`), the console (a side panel at every "add" gesture), the chart
  (`api.proceduresExtra`), `docs/procedures/`, `tools/procedures/export_docs.py`; builds on
  [ADR 0034](0034-connectors-by-capability.md) (types are data), [ADR 0030](0030-an-mcp-door-for-external-clients.md)
  (the door's clients have a page), [ADR 0049](0049-an-artifact-is-typed-stored-and-traced.md) (Markdown is rendered as React
  elements, never as HTML) and [ADR 0058](0058-connector-secrets-are-write-only-values.md) (secrets are typed in the console)

## Context

The console builds a connector's form from its schema. It says nothing about what to create at the
vendor: the SharePoint form asks for a tenant id, a client id and a client secret without saying that the
secret is an Entra app secret, that its **Value** is wanted and not its ID, or that `Sites.Selected`
grants nothing until each site is granted to the app. The Jira form asks for a webhook secret that the
webhook endpoint does not read. The recipes that exist are scattered in five documents, none reachable
from where the values are typed, and none able to show **this** deployment's callback and webhook
addresses. Vincent asked, on 2026-10-16, for one in-app procedure per connector and per external agent
type, shown when one is added.

## Decision

1. **A procedure is a Markdown file in the core**, `packages/core/src/choregos_core/procedures/<id>.md`,
   with a YAML front matter: `id`, `title`, `applies_to` (exactly one of `connector`, `source`, `agent`,
   `door_client`, plus an optional `card_host` or `preset` that makes it a preset), `audience`, `version`
   (+1 on any change of substance), `reviewed` (the day it was compared with the vendor's pages),
   `vendor_docs`, `values`, `egress`, `fields` and `link`. Ids are `connector.<kind>.<type>`,
   `connector.mcp.mcp.<preset>`, `source.<kind>`, `agent.door.<client>`, and, as their types arrive,
   `agent.remote.a2a[.<preset>]` and `agent.code`.
2. **The body has fixed sections, in this order**: *Before you start*, *At <vendor>*, *Permissions*,
   *In Choregos*, *Test it*, *When it fails*; a deployment may append *On this platform*. The fixed
   shape is what lets a test hold fifty files to the same standard, and a person always finds the
   failure table in the same place.
3. **Placeholders are filled by the deployment.** `{{api_url}}`, `{{public_url}}`, `{{org}}`,
   `{{webhook_url:<source>}}` (the routes of `routers/webhooks.py`) and `{{oauth_callback:remote}}`. An
   unknown placeholder is a validation error, never an empty string. `values` lists the ones the console
   shows as copy boxes.
4. **Served by the API**: `GET /procedures` (the index; `?for=connector:documents/sharepoint`, `source:web`,
   `agent:remote.a2a` or `door_client:cursor`, with `card_host` or `preset`, answers the one best match, a
   preset before the generic one) and `GET /procedures/{id}`. Any signed-in member reads them.
5. **Facts are checked, not trusted.** The front matter is validated (pydantic); every link in a body is
   `https` and listed in `vendor_docs`; no secret-shaped string; `fields` must be fields of the type's
   configuration schema or secret fields, and every secret field must be explained, so a renamed field
   breaks the procedure; the GitHub App's permissions are one constant
   (`choregos_adapters.github.APP_PERMISSIONS`) that the adapters' token requests must stay inside and
   the GitHub procedures must list exactly. Where the author could not verify a vendor fact, the
   procedure says so in words ("not proven", "check at …") instead of inventing a menu path.
6. **Coverage is enforced.** `tests/procedures/test_coverage.py` is red when a shipped connector type, a
   knowledge source kind or a client of the integrations page has no procedure, and a test with a
   temporary registered type proves the check can fail.
7. **A deployment adds to them.** `CHOREGOS_PROCEDURES_EXTRA` (chart `api.proceduresExtra`, a ConfigMap
   mounted at `/etc/choregos/procedures`) or a `choregos.procedures` entry point supplies `<id>.site.md`,
   which appends *On this platform* (who registers the vendor app here, where the egress change goes),
   and `<new-id>.md`, which adds a procedure for a plugin's type. A refused file is logged and skipped;
   it never stops the API.
8. **The docs come from the same files.** `tools/procedures/export_docs.py` writes `docs/procedures/`
   (placeholders as `<api_url>`) and an index; `make procedures-check` is part of `make ci`.
9. **Rendered as Markdown sections, never HTML.** The console draws each section with its Markdown
   renderer, adds ordered lists and https-only links allowlisted by the procedure, and shows `values`
   as copy boxes.

## Consequences

- A person adding a SharePoint, a Confluence space, an MCP server or an external agent reads, beside
  the form, what to create at the vendor and which values to copy, with this deployment's own addresses.
- Adding a connector type without its procedure turns the suite red.
- Procedures describe vendor screens as they were on `reviewed`. They go stale; the check that remains
  is a nightly dead-link report, not a proof that the screens still look as described.
- Content is English (ADR 0046). A deployment's notes are in its own language.

## Alternatives discarded

- **Procedures in `docs/` only, linked from the form.** They leave the app, and public docs cannot show
  this deployment's addresses.
- **Structured steps in JSON or YAML.** Heavier to write and review than Markdown sections, no gain.
- **Prose inside `ConnectorTypeSpec`.** Prose in Python that plugins and sites could not extend and the
  docs could not reuse.
- **Rendering HTML.** Refused by ADR 0049.
- **Fetching the vendor's pages live.** Needs the network, and the pages change under us: we link them.
