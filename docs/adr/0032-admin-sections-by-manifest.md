# 0032 — Administration sections are declared by manifest; the console renders them, no plugin code runs in it

- **Status**: accepted, 2026-10-05
- **Concerns**: `greffons.py` (a new seam), `GET /api/v1/ui/admin-sections`, the contract
  `ui-manifest.schema.json`, the console's `/admin`, the enterprise edition; follows
  [ADR 0024](0024-two-editions.md) and [ADR 0029](0029-a-platform-for-governed-agents.md);
  the `ui` seam of the specification (R-SOC-UI-01, SOC-131)

## Context

The product review of 2026-10-05 found `/admin` nearly empty: six cards, and nothing of the
enterprise edition. The enterprise layer has ten features — organisations and their lifecycle,
SAML, SCIM, session revocation, fresh authentication, platform administrators — served by its own
routes since the `declarer_un_routeur` seam, and **no screen at all**. An administrator reaches
them with `curl` or not at all.

Three ways to give them screens:

1. **The enterprise layer ships its own front-end bundle**, loaded by the console (module
   federation, remote script). Every enterprise release then has to match the console's React,
   design system and Content-Security-Policy; and the console executes code it did not build —
   a supply-chain entry point into the page that holds the session and the decisions.
2. **The core console grows enterprise pages behind flags.** The core then knows the enterprise
   features one by one — exactly what ADR 0024 refuses: the community edition must not carry
   the other's shape.
3. **The enterprise layer declares its sections as data**, and the console renders them with
   generic blocks it already owns.

## Decision

1. **A seam, `declarer_une_section_d_administration(manifeste)`**, called by a plugin from its
   `brancher()`. A manifest is **data**: an id, a title, a scope (`platform` or `organisation`),
   the permission it requires, and ordered **blocks**:
   - `form` — a JSON Schema, the path that reads the values and the path that writes them;
   - `table` — the path that lists rows, the columns, and the actions on a row;
   - `action` — a path to call, a label, a confirmation text, `danger`, and `reauth` when the
     gesture requires a fresh authentication (ADR 0030: a decision is never taken on an old
     session);
   - `secret_once` — an action whose answer holds a secret the console shows once and never
     stores (an SCIM token, a client secret).
2. **The core checks a manifest when the platform starts**, against
   `ui-manifest.schema.json`, and checks that every path it names is served by a declared route —
   the core's or the plugin's — with that method. An invalid manifest **stops the start**, like
   two routes on the same path: a section that points nowhere is a broken screen discovered by an
   administrator, at the worst moment.
3. **`GET /api/v1/ui/admin-sections`** returns the sections the caller may see: the permission
   and the scope are filtered on the server, as for any route; the console never hides what the
   API would serve.
4. **The console renders the blocks** under `/admin/x/{section}`, with one shared `SchemaForm`
   (built in the console, not `@rjsf`: bundle budget) that the connector forms reuse later
   (ADR 0034). No plugin code is loaded: the CSP stays `script-src 'self'`.
5. **The community edition says what it lacks, honestly.** Without the enterprise layer, `/admin`
   lists what it would add — organisations, SAML, SCIM, sessions — as text, not as disabled
   fake screens.

## Consequences

- The enterprise layer ships **no front-end**. Its screens follow the design system, the
  accessibility tests and the CSP of the console by construction.
- A need the blocks cannot express — a graph, a live log — is a **new block in the core**, decided
  here, not a script in a plugin. That is deliberate friction.
- The core gains a contract (`ui-manifest.schema.json`) that a plugin's author reads; it is
  versioned like the others (ADR 0001).
- Navigation by product manifests (R-SOC-UI-01) is not decided here; the same format is meant to
  grow into it, project tabs included.

## Addendum (2026-10-21): a block may carry its own permission (S42-02)

A section has one permission and one scope, so a screen mixing an organisation-level form and an
instance-level one (platform administrators only) had to be two sections with the same title. Decision:
every block kind gains an optional `permission`, same vocabulary as the section's
(`ui-manifest.schema.json`, `$defs/block_permission`).

- The section's permission and scope still apply first. `GET /api/v1/ui/admin-sections` then drops the
  blocks the caller lacks (`platform:admin` is checked on the instance, any other permission in the
  organisations in scope) and lists the section only if at least one block remains.
- Start-up refuses an unknown block permission, like a section's.
- Nested `row_actions` are not filtered: they belong to their table.
- `choregos_api.greffons.SECTION_FEATURES` (`frozenset({"block_permission"})`) lets a plugin detect
  the feature without parsing versions. Hiding is a courtesy: the routes a block calls still enforce
  their own permission.
- Not decided here: resetting a form to inherited values; plugins use `action` (DELETE) and `table`
  blocks.
