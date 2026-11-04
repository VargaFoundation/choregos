# Trial, element 8 - results of 2026-10-03

**Question**: can a run receive its token and its secrets **without any Kubernetes Secret**, when the
orchestrator has no right on the Secrets of the run namespace (mechanism (a) of ADR 0005
of the requirements document)? And does the bootstrap fit in a static binary, in an image without Python
(ADR 0015)?

**Answer: yes to both, with a contract correction (the "not ready yet" case).**

## Set-up

- A kind cluster (`kindest/node:v1.36.1`), two namespaces under **PSA `restricted`** (`trial-control`,
  `trial-run`).
- The orchestrator, in `trial-control`, has a Role in `trial-run` limited to `jobs` (create, read,
  delete), `pods` (read, watch) and `pods/log`: **no verb on Secrets**.
- The run's Job carries as an environment variable a **single-use bootstrap code** (32 random
  bytes), never a Secret. The pod runs without a service account token, as non-root, with a read-only
  file system, all capabilities dropped.
- At start-up, the bootstrap (`runtime/`, Rust, **870 KB static binary in busybox**) exchanges the
  code for a run token and its declared secrets, launches the command with the secrets in memory,
  then publishes the result with its token.
- The API stub (`api/app.py`) applies the rules of 03 §12.1; the orchestrator reads the pod's address
  (pod watch) and passes it on: the API has no Kubernetes right.

## What was proved

| # | Check | Result |
|---|---|---|
| 1 | The run receives its secret (32 characters) in memory and succeeds; its result is accepted with its token | ✅ |
| 2 | A **replay** of the consumed code returns **409**, fails the run (`bootstrap_replayed`) and revokes the token | ✅ |
| 3 | A forged code returns **401** | ✅ |
| 4 | A code whose pod started more than 10 minutes ago returns **410** | ✅ |
| 5 | A code presented from an **address other** than the pod's returns **403** | ✅ |
| 6 | The orchestrator can neither create nor read a Secret in `trial-run`; **0 Secrets** in that namespace | ✅ |

To replay: `./run.sh` (about 1 minute once the cluster is created; 5 minutes with its creation).

## The contract correction found by the trial

**The pod calls the bootstrap before the orchestrator has read its address.** On the first run,
the API answered "code expired" (no known start time), and the run failed. A
**retryable** case is needed:
- the API returns **425 `bootstrap_not_ready`** (with `Retry-After`) as long as the pod's address and start
  time are not recorded;
- the bootstrap retries, at most 60 times at one-second intervals; on the trial's second run, it
  succeeded on the second attempt.

To carry over to 03 §12.1, 10 §5.5, ADR 0005 and SOC-093.

## Other findings

- **kind 0.30 no longer loads images into a 1.36 node** (`unknown containerd config version: 4`).
  Workaround used: `docker save ... | docker exec -i <node> ctr --namespace=k8s.io images import -`.
  To take up in the cluster tests (SOC-122) until kind catches up.
- The source of the address the API sees is indeed the pod's address for a pod -> Service call in the
  cluster (kube-proxy in iptables mode does not SNAT here). Behind a proxy or a service mesh,
  the trusted header is needed (`CHOREGOS_TRUSTED_PROXY_HEADER`): not tried by the trial.
- The code stays readable in the pod's specification by whoever can read the namespace's pods: it is the
  known price of mechanism (a), bounded by single use, binding to the address and a validity of
  10 minutes. The replay is detected and fails the run (check 2).
