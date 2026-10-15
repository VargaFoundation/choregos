# Re-publish a tenant's French names in English

Since `joiners-leavers` 2.0.0 (S39-11) the template ships English identifiers; a tenant created
before keeps its French ones (renaming a state under a running work item is impossible, and a
published version is never rewritten). `tools/rename_tenant_names.py` brings such a tenant forward
by **adding** English versions, through the public API only.

## What it does, and never does

Each step is idempotent: a second run finds nothing to do.

1. installs the template's skills (`onboarding-procedure`, `access-profiles`) when absent;
2. installs its agents (`onboarding-coordinator`, `offboarding-coordinator`) when absent;
3. copies each French organisation connector under its English name (`directory`, `devices`,
   `carrier`, `badge-readers`, `supplier`): type, kind, config and **secret references verbatim**
   (nothing to do in the secret store), then the operations' policy and groups, then the projects'
   tightenings; an MCP connector is discovered again (its tools were renamed too);
4. publishes the template's English workflows as the **next version** of a project's HR workflows
   (detected by the French state `a_revoir`), renames the routing label `depart` to `leaver`, and
   translates the state labels of hand-made workflows (`preuve/preuve-agent`) as the next version,
   state ids untouched;
5. renames project display names;
6. moves the French agents to `deprecated` (they still run; a warning shows on pin).

It never deletes, never retires an agent, never renames a project slug (a slug is a URL, and the API
cannot rename one), never rewrites a version, never touches managed objects or the ontology. A work
item stays pinned to the workflow version it was born on. A workflow with open work items is skipped
unless `--allow-open`; the report always lists those items with their console URL.

The mapping is data: `tools/rename_tenant_names.yaml` (`--mapping` for a copy).

## Run it (an organisation administrator's gesture)

1. Release a CE that contains S39-11a and S39-11b, deploy it, so the English template and the
   supplier fake's new tool names are what the API serves.
2. In the console, `/admin` → Tokens: mint an API token for an `org_admin`; keep it in a file
   (mode 600), over NetBird for the dev tenant.
3. Dry run (writes nothing), read the table:

   ```bash
   uv run python tools/rename_tenant_names.py \
     --api https://choregos.internal.dev.diametral.com --org varga --token-file ~/choregos-admin-token
   ```

4. The same with `--apply`; add `--allow-open` when the report says workflows were skipped because of
   open work items (publishing only adds a version, the open items finish where they are).
5. Afterwards, once no work item of a French version is open: retire the French agents in
   `/agents/<slug>` → Lifecycle → Retire, and delete the French connectors in `/admin/connectors`.
   Trial projects (`essai-*`, `preuve`, `rh-demo`) can be deleted from `/p/<slug>/settings` rather
   than kept with English display names.

Run it after the secret-reference migration of S39-06 if that one is not merged yet: the references
are copied as opaque strings, so either order works, but the copies should hold the final format.
