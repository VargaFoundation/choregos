# The demonstration: two projects, one engine

A **single-node** cluster, the platform and all its dependencies deployed by the chart,
and two projects that have nothing in common:

| Project | What the agent does | What it proves |
|---|---|---|
| `panier` | Reads a ticket, writes code and its test in a git repository, pushes a branch | The whole chain: ticket → agent → diff → guarantees → next state |
| `staffing` | Reads an HR need, proposes profiles, qualifies them | The engine does not speak software: same states, same budgets, same guarantees, with the business's playbooks and outputs. **This project has no repository** — the agent works in an empty directory |

Everything that runs here is the platform's real path. **Only one thing is simulated**:
the tracker (no GitHub or Jira on a bench), so the tickets are put in the database by
`seed.py`. The agents, for their part, do real work.

## Bringing up the bench

Allow **about twenty minutes** the first time (building the images), a node with 8 GB and
4 free CPUs, and Docker, kind, kubectl, helm.

```bash
make demo-up        # single-node kind cluster, namespace, git repository, ConfigMaps, chart
make demo-images    # builds local/choregos-*:demo and LOADS them into the kind node
```

The demonstration chart expects local images (`imagePullPolicy: Never`): without
`make demo-images`, every pod stays in `ErrImageNeverPull`. The chart embeds PostgreSQL,
Temporal, memory (Ecphoria) and the LLM gateway (LiteLLM) — that is what
`global.<dependency>.embedded` means.

## Giving the agents model access

The demonstration goes through **the embedded gateway**: the platform mints a key per run,
capped, and counts the spend in the ledger — it is the only mode where the cost per ticket
is a measured figure and not an empty cell. The gateway needs a provider key, which only
it sees:

```bash
kubectl -n choregos create secret generic platform-llm-key --from-literal=api-key=sk-ant-…
kubectl -n choregos rollout restart deploy/choregos-litellm
```

Without a provider key (a Claude Code subscription only), the bench runs in **direct**
mode: the agent brings its own credentials, and **nothing is counted or capped** beyond
turns and minutes. Say so when showing the dashboard.

```bash
kubectl -n choregos create secret generic agent-creds \
  --from-literal=CLAUDE_CODE_OAUTH_TOKEN="$(jq -r .claudeAiOauth.accessToken ~/.claude/.credentials.json)"
helm upgrade choregos charts/choregos -n choregos -f charts/choregos/values/local.yaml \
  -f demo/values-demo.yaml --set global.gateway.embedded=false --reuse-values
make demo-seed GATEWAY=direct
```

In both cases the secret is mounted **by reference** in the pods, never copied into a
specification.

## Setting up the projects and the tickets

```bash
make demo-seed
```

The Job sets up the organisation, the two projects, their workflows and their tickets,
then starts each ticket — the equivalent of `mark_agent_ready` in the API: one Temporal
workflow per ticket, which reads the state, picks the transition and launches the agent.

## Knowing it worked

```bash
make demo-status
```

What you should see, and how soon:

| What | Expected | When |
|---|---|---|
| `DEMO-1..3` | `done` | ~10 min |
| `RH-1`, `RH-2` | `a_valider` (waiting for the manager) | ~5 min |
| ledger, `kind=model` | **non-zero** tokens and euros | from the first run |
| ledger, `kind=tool` | at least one row (`verifier_adresse`, free) | at the first sourcing |

A ticket that stays in its starting state for more than 15 minutes is not waiting: it is
dead. `kubectl -n choregos exec deploy/choregos-temporal -- temporal workflow describe -w
wi-staffing-RH-1` says why. (What the interface should say itself — workstream P0-2 of the
2026-09-24 state of the project.)

## What this bench does not prove

The SCM connector is a `fake`: it cannot compare two branches. The guarantees that read the
diff — scope respected, diff size, absence of secrets — therefore **refuse to give a
verdict** rather than pass vacuously, and the demonstration workflow does not declare them.
On a real repository (GitHub) they come back: it is a limit of the bench, not of the
engine. The rest is real, including the verification of the execution evidence.

## Replaying the demonstration

A ticket has **only one** interpreter: Temporal refuses to restart a workflow that has
already finished under the same identifier (`ALLOW_DUPLICATE_FAILED_ONLY`). That is
intended — it is what stops the same ticket being handled twice. Replaying therefore takes
**new tickets**: `make demo-seed SERIE=b` puts down `DEMO-1b`, `RH-1b`… next to the
previous ones.

Reset the demonstration repository between two attempts:

```bash
kubectl -n choregos exec deploy/demo-git -- sh -c \
  'cd /srv/git/app.git && for b in $(git for-each-ref --format="%(refname:short)" refs/heads | grep choregos/); do git branch -D $b; done'
```

## Watching

```bash
kubectl -n choregos logs -l choregos/run-id --tail=50 -f         # the agents at work
kubectl -n choregos port-forward deploy/choregos-web 3000:3000   # the front end
```

The demonstration repository can be read back from the cluster:

```bash
kubectl -n choregos exec deploy/demo-git -- git -C /srv/git/app.git log --oneline --all
```
