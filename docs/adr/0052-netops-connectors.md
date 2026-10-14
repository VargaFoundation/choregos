# 0052 — Netops connectors: pfSense, Cilium and Hubble, nginx — read through the platform, changed only by a governed action or a pull request

- **Status**: accepted, 2026-10-12
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
   release train carries to production (ADR 0006). No path applies to a cluster.

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
