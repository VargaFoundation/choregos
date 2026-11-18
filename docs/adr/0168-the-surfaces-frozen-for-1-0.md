# 0168 — The surfaces frozen for 1.0: REST, MCP, A2A, triggers and the enterprise seams

- **Status**: accepted, 2026-11-15 (the product owner's decision D14 of the graal and Choregos offer of
  2026-11: the two doors between the products are MCP and A2A, and 1.0 freezes what a client can depend on)
- **Concerns**: `packages/contracts` (`openapi.yaml`, the schemas), `apps/api/src/tranibor_api/mcp/`,
  `apps/api/src/tranibor_api/a2a/`, the trigger route (ADR 0113), `edition.py`, `extensions.py` and
  `tenant_sign_in.py`; follows [ADR 0001](0001-frozen-contracts.md) (what a contract is),
  [ADR 0024](0024-two-editions.md) (the seams), [ADR 0030](0030-an-mcp-door-for-external-clients.md),
  [ADR 0078](0078-governed-workflows-as-a2a-skills.md) (the A2A door),
  [ADR 0079](0079-choregos-and-graal-are-autonomous-products.md) (public interfaces only),
  [ADR 0113](0113-a-workflow-starts-from-a-trigger.md); companion of [ADR 0169](0169-the-mcp-profile-between-products.md)

## Context

Choregos is at 0.27. Until now a surface was "frozen" in the sense of ADR 0001: a change goes through a
`contract-change` PR and the types are regenerated. That is a review rule, not a promise to a client. The
project has broken clients on purpose when it was cheaper than carrying them (the French aliases, removed in
0.24, ADR 0098), and nothing written says how long a removed route, tool or field keeps answering.

The offer of November 2026 changes who depends on us. A second product, an application of a customer, an
assistant of a person and an enterprise edition built in another repository all call surfaces of this
repository without being able to read its tests. The offer targets a 1.0 on 2027-04-30 "with REST, MCP, A2A
and the seams frozen" (step U2); this record says what that sentence means, so that the date is a test and
not a mood. It is a *target*: no date is promised to anyone by this record.

What exists today, read in the code on `main` (0.27.6), and what does not:

| Surface | What is there | What is not there |
|:--|:--|:--|
| REST | `packages/contracts/openapi.yaml`, served under `/api/v1`; a test checks that the API implements exactly the contract's paths and `operationId`s (ADR 0001); `make contracts-check` | no written compatibility rule, no deprecation window, no check that an operation or a field was not *removed* between two releases |
| MCP door | `POST /mcp` and `/mcp/assistants/{assistant}`; protocol versions `2025-11-25`, `2025-06-18`, `2025-03-26` (`choregos_core.mcp.VERSIONS`); eleven tools on the project door (`docs_search`, `docs_read`, `list_projects`, `describe_workflow`, `create_work_item`, `search_work_items`, `get_work_item`, `summarize_run`, `list_pending_decisions`, `get_pending_decision`, `draft_decision`); scopes `mcp:read` and `mcp:write`; OAuth resource server (RFC 9728) or a `chg_` token | no file that lists the tools and their schemas; no test that fails when one disappears or changes |
| A2A door | card at `/a2a/orgs/{org}/.well-known/agent-card.json`, JSON-RPC at `/a2a/orgs/{org}`; A2A 0.3.0 and 1.0 at the same URL (`a2a/v1.py`); the `skill-fields/v1` card extension (`https://choregos.dev/a2a/extensions/skill-fields/v1`); the `a2a` token scope; machine accounts (ADR 0131); `apps/api/tests/test_a2a_guide.py` replays the documented calls | `ListTasks` and `GetExtendedAgentCard` answer an error (`-32004`, `-32007`); the *result* of a workflow over A2A is not fixed (no ADR names `outcome`, `confidence`, a size); the guide test is not a suite a third-party caller can run |
| Triggers | `POST /webhooks/triggers/{org}/{project}/{name}`; token, or HMAC signature; mapping by JSON pointers; the `received_events` outcomes (ADR 0113) | proven against no real sender (STATUS S56-01) |
| Enterprise seams | `edition.declare`, `declare_group_mapper`, `declare_session_validation`, `declare_platform_administrator`, `tenant_sign_in.declare_sign_in_resolver`, `extensions.declare_router`, `declare_admin_section` and the others of `extensions.py` (ADR 0024, 0032, 0108, 0130); `packages/adapters/tests/test_extensions.py` installs a real `.dist-info` | two seam names are still French and `docs/deployment.md` ("Upgrading to 0.24") says they stay so until the enterprise edition and the core rename them together; the enterprise edition is built against the core by a coordination issue (#1229), not by a compatibility test |

## Decision

1. **1.0 freezes five surfaces, each named in the table above**: the REST contract, the MCP door, the A2A
   door, the trigger route, and the seams the enterprise edition attaches to. Nothing else is promised: the
   console, the CLI's text output, the chart's internal templates, the runner's sidecar tools (ADR 0017), the
   plugin server protocol and the outpost door (a contract of its own, ADR 0148) keep changing under their own
   rules.
2. **What "frozen" means, the same for the five.** From 1.0, inside a major version:
   - what is *added* is free: an optional field, a new operation, a new tool, a new skill field;
   - what is *removed, renamed, retyped, or made required* is a break;
   - a break does not edit the old surface in place. It creates a new one beside it, under a new name or
     URI (`/api/v2` or a new `operationId`, a tool `<name>_v2`, a new extension URI, a new A2A protocol
     version served at the same URL), and the old one is **served for at least 90 days** after the release
     that announces the new one, with the announcement in the release notes and, where the protocol allows
     it, in the response (a `Deprecation` header on REST);
   - a surface that cannot be served beside its successor (a seam called once at start-up) breaks only at a
     major version, and the release notes say so first.
3. **Frozen is a guard, not a sentence** (CLAUDE.md: nothing is declared done without a test that fails in
   its absence). A surface counts as frozen in `docs/plan/STATUS.md` only when a test turns red if it moves.
   The guards this record asks for, each its own story, none built by this record:
   - REST: the contract of the previous release, kept in the repository, and a test that every operation,
     required field and enum value of it still exists in the current one;
   - MCP: the profile of [ADR 0169](0169-the-mcp-profile-between-products.md), compared with what `tools/list`
     announces;
   - A2A: the guide test exported as a suite any caller can run against any door (the offer's C5), and a
     test that the card still carries both versions and the extension;
   - triggers: the existing trigger tests, plus one that pins the outcomes of `received_events`;
   - seams: a test that imports each seam by its public name and signature, run against the previous
     release's names.
   Until a guard exists, STATUS says "declared, not guarded" for that surface.
4. **The two French seam names are renamed before the freeze, not after**, together with the enterprise
   edition, through the window ADR 0098 already used. A French name frozen at 1.0 would be carried for ever.
5. **Choosing a version is the caller's.** A client that sends a protocol version the door serves gets it
   (MCP's `protocolVersion`, A2A's method names and `A2A-Version` header); a version the door does not serve
   is refused with the protocol's own error (`-32009` for A2A), never silently served in another.
6. **The result of an A2A call is fixed by a later record**, not here: a small JSON artefact whose fields
   (`outcome`, `confidence`, the workflow's outputs) and size bound the calling product can rely on (the
   offer's C6). Until it lands, a caller reads the artefacts of the work item as ADR 0078 describes them, and
   no product may build a route on a field this record did not freeze.
7. **The seams the enterprise edition attaches to are frozen as they are on the day of 1.0.** The edition
   boundary itself moved on 2026-11-15 (SAML sign-in, re-authentication and the revocation of a person's
   sessions pass to the community edition; SCIM stays in the enterprise edition, see the amendment to
   [ADR 0024](0024-two-editions.md)). That moves *code across a seam*, and it must land **before** the
   freeze: after 1.0, a seam is no longer free to be reshaped for it.

## Consequences

- A product, an application or an edition built on one of the five surfaces can say what it depends on and
  how long a break is carried. This is what the offer's U2 asks of the ADR.
- 1.0 cannot be released while a surface lacks its guard, or while the table above still has a French name.
  The date is a target, and this record gives the reasons it may slip.
- A removal costs 90 days of carrying the old surface. The cost is accepted for these five surfaces and
  for no others.
- The MCP profile, the A2A suite and the REST comparison are three pieces of work that do not exist; STATUS
  carries them as `⬜`.

## What this record does not decide

- Who signs a release 1.0, and the date: the owner's, on the evidence of the guards.
- The REST major-version scheme (`/api/v2` beside `/api/v1`, or per-operation versions): decided with the
  first break that needs it.
- The long-term-support policy of qualified upgrades, which belongs to the offer, not to the code.

## Alternatives discarded

- **Freezing everything the contract names.** The contract also holds operations written for the console,
  which no outside client is meant to call; freezing them would slow the console down for no reader. Which
  operations are in the frozen set is a list the REST guard of decision 3 has to carry; this record does not
  draw it.
- **A compatibility promise in prose only.** The project's rule is that a feature whose guard can be removed
  without turning the suite red is hoped for; so is a promise.
- **A shorter window than 90 days.** The other product calls us from a platform it deploys
  continuously and publishes no versions for; 90 days is the figure the offer proposes, and the longest we
  commit to without a customer asking for more.
