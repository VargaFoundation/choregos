---
id: connector.network_policy.cilium
title: Connect Cilium and Hubble
applies_to: {connector: network_policy/cilium}
audience: org_admin
version: 1
reviewed: 2026-10-16
vendor_docs:
  - {title: "Cilium network policy", url: "https://docs.cilium.io/en/stable/security/policy/"}
  - {title: "Setting up Hubble observability", url: "https://docs.cilium.io/en/stable/observability/hubble/setup/"}
egress: ["the Kubernetes API server", "Hubble Relay"]
fields:
  kubernetes_url: "the cluster's API server, https"
  token: "the read-only service account's token, typed once"
  ca_cert: "PEM of the cluster's CA"
  hubble_server: "Hubble Relay's address, hubble-relay.kube-system.svc:443 by default"
  hubble_client_key: "the mTLS client key, if Relay requires one"
  gitops_repo: "owner/repo where policy changes are proposed"
  gitops_token: "a token that can open a pull request there, typed once"
---

## Before you start

- A cluster running Cilium, with **Hubble Relay** if you want flows. The platform reads policies from the
  Kubernetes API and flows through the `hubble` CLI against Relay (the CLI ships in the API and worker
  image, never in the runner).
- A GitOps repository where policy changes are proposed as pull requests. **No path applies a policy to a
  cluster**: a change is a pull request that your release process carries.

## At the cluster

1. A dedicated **read-only service account**, never the pod's own token, with a `ClusterRole` that can
   `get` and `list` `ciliumnetworkpolicies` and `ciliumclusterwidenetworkpolicies` (`cilium.io`), and a
   binding. Create a token for it.
2. If Hubble Relay requires mTLS, issue a client certificate and key for the platform.
3. A token for the GitOps repository that can push a branch and open a pull request.

## Permissions

- Kubernetes: `get` and `list` on the two Cilium policy kinds, nothing else.
- Relay: read of flows.
- GitOps repository: branch and pull request only. The platform never merges it.

## In Choregos

1. Administration, **Connectors**, **Declare**: capability **Network policy**, type **Cilium**.
2. `kubernetes_url`, `ca_cert`, and `token` (password field). `verify_tls` stays true; `false` is for
   development only.
3. For flows: `hubble_server`, `hubble_tls`, `hubble_ca_cert`, `hubble_tls_server_name`, and for mTLS
   `hubble_client_cert` and `hubble_client_key`.
4. For proposals: `gitops_repo`, `gitops_base` (default `main`), `gitops_path` (default `cilium`) and
   `gitops_token`.

## Test it

Press **Test**, then call `list_network_policies` and `get_flows` from a workflow.

## When it fails

- `Forbidden` from the API server: the role is missing or not bound to the account.
- A TLS error: set `ca_cert` (cluster) or `hubble_ca_cert` (Relay).
- No flows: Relay is not reachable, or requires mTLS you did not configure.
- A proposal that fails: `gitops_token` cannot push to `gitops_repo`.
