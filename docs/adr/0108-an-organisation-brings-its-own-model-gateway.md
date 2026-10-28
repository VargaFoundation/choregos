# 0108 — An organisation brings its own model gateway, and its runs use it

- **Status**: accepted, 2026-10-28 — implemented by S44-04
- **Concerns**: `services/gateway.py` (`org_gateway`), `createOrgConnector` (`routers/org_connectors.py`),
  the orchestrator's `load_project` (`activities/base.py`) and `prepare_stage` (`activities/stage.py`),
  the console's Models and Installed connectors pages; extends [ADR 0060](0060-a-gateway-agnostic-model-catalogue.md)
  (the model catalogue) and [ADR 0034](0034-connectors-by-capability.md) (connectors by capability)

## Context

ADR 0060 already read an organisation connector of kind `gateway`, but only for the catalogue: the
Models page listed and synced that gateway, while every run minted its key on the deployment's
gateway and its agent called the deployment's `gateway_url`. Nothing in the console could declare
such a connector either: `gateway` is one of a project's delivery kinds, which the organisation's
form leaves out. And the API built that gateway without reading its sealed secrets, so a key typed
in the console reached the adapter as a bare `store:` reference. An organisation asked to connect
its own LLM gateway (2026-10-28) and saw no model on the Models page.

## Decision

1. **The organisation declares its gateway as a connector of kind `gateway`**, from Installed
   connectors (the Models page links there): `litellm` or `openai-compatible`. At most one per
   organisation (`409` for a second). The `direct` gateway is refused in staging and prod, as it is
   for a project: it measures no cost and enforces no cap.
2. **The organisation's gateway serves its runs.** A project that declares no gateway of its own
   builds its gateway adapter from the organisation's connector (secrets read through the store),
   so the run's key is minted there, and the resolver gives the agent that connector's `base_url`.
   A project's own gateway still wins, and its `base_url` is given to the agent the same way: an
   agent calls the gateway its key comes from. An agent session (ADR 0080), whose key comes from the
   organisation's gateway, calls it there too, from the API's prompt loop and from a hosted pod.
3. **The catalogue builds that gateway with its secrets resolved** through the store, like any
   connector built for use.
4. **The Models page says whose gateway it is** and, in `open` mode, shows what the gateway lists
   that the catalogue does not hold yet.

## Consequences

- The gateway's host must be reachable from the runner pods: the organisation's egress list records it
  when the connector is declared, and the cluster's network must let it through (the Network page
  lists what the platform must still open). Choregos does not change the cluster's policy.
- A hard budget (team, capped key) exists only where the gateway can enforce one (`litellm`); on
  `openai-compatible` the budgets are Choregos's own, as the Models page already says.
- A gateway connector without a `base_url` (a `litellm` one reading `CHOREGOS_GATEWAY_URL`) keeps the
  deployment's URL.
