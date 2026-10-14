# 0052 — Netops connectors: pfSense, Cilium and Hubble, nginx — read through the platform, changed only by a governed action or a pull request

- **Status**: accepted, 2026-10-12; amended 2026-10-13 (§6-7, governed writes, S33-09; §7, approval by a group, #401)
- **Concerns**: `choregos_adapters.netops` (three connector types and their fakes), the broker
  (ADR 0034), the `connector.call` effect (ADR 0035), the catalogue agent `netops` (ADR 0040), the
  tool `validate_nginx_config`, the API/worker image (Hubble CLI) and the runner image (nginx), the
  agent evals (ADR 0020); decision D3 of the plan of 2026-10-12

## Context

Decision D3 makes netops a separate agent, for our own estate first: a pfSense at the edge, Cilium
in the clusters (its `CiliumNetworkPolicy` objects and its Hubble flows), nginx as reverse proxy.
The questions it answers — "why is this flow dropped?", "which rule lets this in?" — need the live
state; the changes it proposes touch the perimeter. Nothing in the platform reads any of it today,
and nothing must let an agent write to it directly.

## Decision

1. **Three connector types, by capability** (ADR 0034): `firewall: pfsense`,
   `network_policy: cilium` (capabilities `network_policy`, `flows`) and `web_server: nginx`. Each
   declares its configuration schema, its secret fields (written as references) and its operations;
   each has a scriptable fake that speaks the protocol of the real system, so that the tests run the
   REAL connector (ADR 0005).
2. **Reads are operations, allowed, served through the broker as MCP tools.** The key stays in the
   platform; a run sees `<connector>__<operation>` under the selection of its agent.
   - **pfSense** through the REST API package (pfrest, `pfSense-pkg-RESTAPI`, v2):
     `GET /api/v2/firewall/rules`, `/firewall/aliases`, `/firewall/nat/port_forwards`, `/interfaces`,
     `/status/gateways`; key in `X-API-Key`, of a pfSense user whose privileges are those pages.
     The `{code, status, data}` envelope stays in the connector.
   - **Cilium** through the Kubernetes API with a **dedicated read-only service account** — the
     connector never borrows the pod's own token — and a client that has `get` and nothing else:
     ```yaml
     apiVersion: rbac.authorization.k8s.io/v1
     kind: ClusterRole
     metadata: { name: choregos-netops-reader }
     rules:
       - apiGroups: [cilium.io]
         resources: [ciliumnetworkpolicies, ciliumclusterwidenetworkpolicies]
         verbs: [get, list]
     ```
   - **Hubble flows through the `hubble` CLI** against Hubble Relay (`hubble observe -o jsonpb`
     with `--namespace`, `--pod`, `--verdict`, `--since`, `--last`; TLS, mTLS by reference). Relay
     speaks gRPC only; a Python client would need `grpcio` (absent from the platform) and the code
     generated from `observer.proto` and `flow.proto`, kept in step with Cilium. The CLI is the same
     call, maintained upstream, and its jsonpb output is ONE format for three uses: what the
     connector parses, what the fake replays (`fakes/recordings/*.jsonl`) and what an operator
     records. The binary is pinned and checksum-verified in the **API/worker image**, where
     connectors run — never in the runner, which has no route and no credential to Relay. Flows are
     normalised (who, whom, port, verdict, drop reason, denying policies) before an agent reads them.
   - **nginx** from the GitOps repository (ADR 0009), not from the live server: files under one
     root, at one ref, through the forge's API. Validation is `nginx -t`, which needs the binary and
     the whole tree: it is the runner-side tool `validate_nginx_config`, on the agent's workspace;
     the runner image carries nginx. Files named by absolute path that exist only on the server
     (certificates) are reported by nginx as missing — the report is shown, not hidden.
3. **TLS is verified, never turned off silently.** A private CA is given as `ca_cert` (PEM).
   `verify_tls: false` is accepted in `dev` only, logged as `netops.tls_verification_disabled` each
   time the connector is built, and refused in `staging` and `prod`.
4. **The `netops` catalogue agent** diagnoses from flows, policies and rules, proposes every change
   as code and never writes directly. Its output is one markdown report, `netops_report_markdown`,
   with `## Findings`, `## Proposed change`, `## Risk`, `## Rollback`, in that order.
5. **An agent eval** (ADR 0020) checks the agent from a recorded Hubble trace: its instructions
   without a model (emptied, they fail), and its answer by predicates on facts the recorded evidence
   supports — the drop reason, the peers and port, the policy that selects the destination — with
   forbidden content (`kubectl apply`, `fromEntities`). Without a gateway the answer is `not run`.
6. **Writes** (S33-09) are never a connector's direct call: a pfSense rule change is a governed
   action that a person approves, with a compensation that restores the previous rule; a Cilium
   policy or an nginx configuration change is a pull request to the GitOps repository, which the
   release train carries to production (ADR 0006). No path applies to a cluster. Four mechanisms,
   general to any connector type, carry it:
   - **`human_approval`** on an operation: its policy never goes below `approval` — the
     organisation's administrator cannot set it to `allowed` (422), and a row forced to `allowed`
     behind the API is still floored by the broker and by the effect's policy (ADR 0034 §7 would
     otherwise let the policy approve it);
   - **a declared `compensation`**: the type says what undoes the operation, rendered with its own
     `result`; the broker attaches it to the action it proposes. pfSense: a created rule is deleted;
     an updated or deleted rule is put back from the `snapshot` its write returned, at its recorded
     position (pfrest `placement`) — a recreated rule gets a new `tracker`, as pfSense assigns it;
   - **platform arguments**: `action_key` (the action and the call, hashed — stable across retries)
     and `work_item_key`, filled by the `connector.call` effect after the agent's arguments were
     checked, never accepted from the agent. A pfSense write marks the rule's description with
     `[choregos:<hash of the key>]`, so a replay after a lost answer finds what it did; if that replay
     finds the change already made, the previous rule is no longer known and the write says so
     (empty snapshot): its compensation then fails, named, instead of restoring a wrong rule;
   - **an unapproved action writes nothing**: `connector.call` refuses an action whose status is
     not approved (or running), even if its `ActionWorkflow` were started by something else.
   A GitOps change lands on `choregos/<number>-netops-<key>` when the action's work item belongs to
   the GitOps repository: the forge's webhook maps the merged pull request back to that work item,
   which boards the train its workflow takes (ADR 0041). Otherwise it lands on `netops/<key>`, and
   the repository's own process merges it. The platform never merges it.
7. **Who approves** is the action's approval rule (ADR 0035): a rank, in a fresh human session,
   never the agent's owner — and, since #401, optionally a **group**. An approver entry
   `{role, group, min}` names an identity-provider group the decider must ALSO belong to: the rank
   says how much someone may decide, the group who is competent to. It reuses the model of ADR 0044,
   where a human actor is named by its `group`. The organisation sets an operation's
   `approver_group` (`PATCH …/operations/{op}`, writes only); the broker then proposes the action
   with `[{role: developer, group: <approver_group>}]` — without one, the project's owners decide.
   A project owner outside `netops-approvers` is refused (403).
   The groups are the identity provider's, as the OIDC sign-in receives them (`groups` claim), now
   kept on the user (`users.groups`), replaced at each sign-in, and carried by the principal. Until
   then they were turned into roles and dropped. A group withdrawn at the IdP therefore holds until
   the person's next sign-in. A development sign-in sets none. A sign-in protocol brought by a
   plugin (`terminer_la_connexion`) passes its groups the same way.
8. **A human actor's `group` is enforced** (ADR 0044 left it informative). `POST /work-items/{id}/decisions`
   reads the work item's workflow at the request's transition: when its actor is a human with a
   group, the decider must carry that group (`403` otherwise), on top of the rank. An
   **organisation administrator may override**, and the override is audited
   (`workitem.decision.group_override`, with the group and the transition): without a way out, a
   group the identity provider never sends would leave the ticket waiting forever. Steps of agent
   actors (questions, scope changes) have no group and are unchanged. Validation warns about the two
   ways to get there: `actor.group_empty` (stateless, `POST /workflows/validate`) and
   `actor.group_unknown` — nobody in the organisation has ever carried the group — from the
   project-scoped `POST /projects/{id}/workflows/validate`. Both are warnings, not errors: an
   organisation may declare a group before its first member signs in. A dev sign-in carries no
   groups, so on a dev stack only administrators answer such steps.

## Consequences

- The netops agent works on fakes end to end (`CHOREGOS_FAKES=1`), and on the real estate once an
  administrator declares the three connectors with their references.
- The API/worker image grows by the Hubble CLI (~30 MB); the runner image by nginx (Debian's).
- A Hubble version that changes its jsonpb output breaks the parser; the recorded trace is the
  regression test, and a new recording is the update.
- The broker serves these reads to any agent whose version selects the connector; the agent's
  selection (ADR 0033) is what keeps a developer agent from reading the firewall.

## Alternatives discarded

- **A gRPC client to Relay in Python**: a native dependency and generated code for one call.
- **Hubble CLI in the runner image**: the agent's pod would need a route and a credential to
  Relay — the opposite of ADR 0034's broker.
- **Reading nginx from the live server** (SSH, an exporter): the merged configuration is the truth
  (ADR 0009), and a live read needs a credential on the server.
- **`verify=False` with a warning only**: a warning nobody reads is a silent default in prod.
