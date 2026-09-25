# An `agent-ready` ticket did not start

## How to know it is this

The `agent-ready` label has been on the ticket for several minutes, and:

- the ticket does not appear in `/p/{slug}` nor in `GET /projects/{id}/work-items`;
- no Choregos status comment was written on the ticket;
- on GitHub, *Settings → Webhooks → Recent Deliveries* shows a red delivery (401, 5xx,
  timeout) — or no delivery at all.

If the ticket **exists** in Choregos but stays in `inbox`, this is not the runbook: see
[run-bloque.md](run-bloque.md).

## What should happen by itself

The webhook is the nominal path; the catch-up is the net. A `TrackerReconciliation` loop
per project re-reads the tracker's candidates every `CHOREGOS_RECONCILE_INTERVAL_SECONDS`
seconds (60 by default) and creates what is missing. **Waiting one minute before acting**
settles most cases.

```bash
temporal workflow query --workflow-id reconcile-<slug> --type status
# {"passes": 412, "last": {"candidates": 3, "created": [], "started": []}, "stopped": false}
```

`passes` must increase. `last.candidates` is what the tracker returns: if it is 0 while the
label is set, the problem is in the tracker or the connector, not here.

## Act

**1. Force an immediate catch-up**

```bash
temporal workflow signal --workflow-id reconcile-<slug> --name reconcile_now --input '{}'
```

**2. The loop does not exist or stopped** (`stopped: true`, or workflow not found):

```bash
kubectl -n choregos rollout restart deploy/choregos-orchestrator
```

The worker restarts the loops of active projects at start-up. The start is idempotent: it
does not create a second loop for a project that already has one.

**3. The loop runs but `candidates` stays empty** — the connector does not see the ticket:

```bash
choregos connectors test --project <slug> --kind tracker
```

Check that the label is exactly `agent-ready`, that the issue is open, and that the GitHub
App has access to the repository. An expired private key shows up in
[rotation-secrets.md](rotation-secrets.md).

**4. Fix the cause on the webhook side**: redeliver the red deliveries from GitHub
(*Redeliver*), and check the shared secret if they come back as 401.

## Check it is fixed

- `GET /projects/{id}/work-items` lists the ticket;
- the ticket carries a Choregos status comment;
- the delivery replayed from GitHub answers 202.

## Do not

Create the ticket by hand in the database. The workflow id derives from the ticket key: a
ticket inserted by hand without `temporal_wf_id` will be picked up by the catch-up anyway,
and two concurrent insertions leave an orphan row to clean.
