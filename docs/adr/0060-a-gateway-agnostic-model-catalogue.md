# 0060 — A gateway-agnostic model catalogue

- **Status**: accepted, 2026-10-15
- **Concerns**: the gateway protocol (`GatewayAdapter`), the model resolver, the API (agents,
  project models, knowledge), the orchestrator's stage preparation and cost ledger, `/admin`;
  builds on [ADR 0047](0047-a-budget-is-a-reservation.md) (a budget is a reservation),
  [ADR 0033](0033-agent-registry-and-skill-library.md) (agent registry) and [ADR 0034](0034-connectors-by-capability.md)
  (organisation connectors)

## Context

Choregos is attached to an LLM gateway, LiteLLM by default. Until now every model the gateway lists
was usable by every agent of every project, nothing said which models an organisation accepts, and
`/platform/models` asked LiteLLM whatever gateway a deployment configured. The request of
2026-10-15: `/admin` must list the usable models and limit them, notably for agents, and it must not
depend on LiteLLM alone.

## Decision

1. **The protocol is complete, not renamed.** `GatewayAdapter` stays (plugins import it);
   `LlmGateway` is its documented alias. It declares `capabilities` (`list_models`, `virtual_keys`,
   `hard_budget`, `spend_readback`, `teams`, `pricing`) and a `test()`; a conformance test refuses a
   declaration the behaviour contradicts. (S39-07a.)
2. **An OpenAI-compatible adapter** (`gateway/openai-compatible`) lists models through
   `GET {base_url}/models`, with two secrets: `api_key` (discovery) and `run_key` (what runs
   receive). With such a gateway **the budget is enforced by Choregos only** (the reservation, turns
   and minutes): no cap at the source, no measured cost. The `run_key` is shared by runs: the operator
   scopes it at the gateway. (S39-07a.)
3. **An organisation model catalogue** (`model_catalogue`), filled from the organisation's gateway
   (sync) plus manual entries for a model the gateway routes but does not list. Per entry: `enabled`,
   `allowed_roles` (stage roles; empty = all), `usages` (`agent`, `embed`, `distill`, `eval`; default
   `agent`), a price override (USD per 1k tokens), a display name, notes, and a snapshot of what the
   gateway said. A sync never deletes: a model that leaves the gateway gets `missing_since` and keeps
   its settings.
4. **Two modes** in `Organization.settings["models"]["mode"]`: `open` (default, today's behaviour:
   what the gateway lists is usable; the catalogue describes, restricts what it has an entry for, and
   overrides prices) and `curated` (only enabled entries are usable; a model discovered later arrives
   disabled). Switching to `curated` is an explicit gesture, preceded by a preview of the agents and
   project profiles it would refuse. Nothing changes for an existing tenant until an admin acts.
5. **Tiers per organisation**: `settings["models"]["tiers"]` maps `cheap`, `standard`, `strong`,
   `aux`, `embed`, `local` to a model name or null. Resolution: project profile, then **organisation
   tier**, then platform profile (`platform/<tier>`). Null keeps the platform alias.
6. **Enforcement, all refusals naming the model and who disabled it**: `ModelResolver` takes a
   `ModelPolicy` and checks the resolved name *and* the model behind an alias, for the stage's role
   and the usage; it is **never bypassed by `allow_unvalidated`** (that is about the eval matrix, not
   about what the organisation allows). The refusal is a `ModelResolutionError`: the interpreter
   already treats it as non-retryable and escalates, so nothing under `workflows/` changes. The same
   policy is checked when an agent version is published or tried (422), when a project's profile is
   written (422), when documents are embedded and when a knowledge base is distilled. The run's gateway
   key still names only the resolved model.
7. **Prices**: an override replaces the gateway's figure in the console and, when the gateway reports
   tokens, in the ledger (tokens x the override); the gateway's own figure is kept on the run
   (`gateway_cost_usd`, `priced_by`). A gateway without `spend_readback` leaves the line `unmeasured`
   and an override changes nothing.
8. **Which gateway is the organisation's**: an organisation connector of kind `gateway` if there is
   one, else the deployment's (`CHOREGOS_GATEWAY_TYPE`, default `litellm`). A project that declares
   its own `gateway` connector keeps it for its runs; the catalogue still applies by model name.

The contract keeps `litellm_model` (in `ModelRef`, `GatewayModel`, `ModelProfile`): it is documented
as "the model name the gateway routes". Renaming it would be a breaking change for no behaviour.

## Capabilities per adapter

| Capability | `litellm` | `openai-compatible` | `direct` | `fake` |
|---|---|---|---|---|
| `list_models` | yes | yes | no | yes |
| `virtual_keys` | yes | no | no | yes |
| `hard_budget` | yes | no | no | yes |
| `spend_readback` | yes | no | no | yes |
| `teams` | yes | no | no | yes |
| `pricing` | yes | no | no | yes |

## Alternatives rejected

- **Rename to `LlmGateway` and `litellm_model` to `model`**: churn across contracts, plugins and replay
  payloads for no behaviour.
- **Enforce only at the gateway** (LiteLLM key `models`): impossible without virtual keys and invisible
  in the console; Choregos refuses before minting.
- **Keep the catalogue in the deploy's `litellm.yaml`**: that is the operator's routing, vendor
  specific, and not editable from `/admin`.
- **Choregos as its own metering proxy** for other gateways: re-implementing a gateway.
- **`allowed_roles` as RBAC roles**: models are chosen for stages, not for people.

## Consequences

- A new permission, `model:catalogue:manage` (org_admin); every member reads the catalogue and the
  usable list.
- Limits, stated: catalogue agents installed from the built-in catalogue are not checked at install
  (they are at run time); a project's own `gateway` connector is not described by the organisation
  catalogue; the `eval` usage is recorded but no eval runner consults it yet.
- Future adapters, each a plugin registering `register("gateway", "<type>", spec)` and bound by the
  conformance suite: Portkey (virtual keys with budget limits), OpenRouter (provisioned keys with a
  credit limit), Kong AI Gateway (consumers and token rate limits), Envoy AI Gateway (token rate limits
  through Envoy Gateway policies), Cloudflare AI Gateway, Azure API Management AI gateway.
