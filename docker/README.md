# Choregos images

| Image | Content | Specificity |
| :-- | :-- | :-- |
| `choregos-api` | FastAPI API + Alembic migrations | non-root, read-only file system |
| `choregos-orchestrator` | Temporal workers | `worker` target of `api.Dockerfile` — same build, same commit |
| `choregos-tools` | MCP sidecar | holds nothing but the run's token |
| `choregos-web` | Next.js front end | production build, strict CSP |
| `choregos-runner` | an agent's workspace | pinned ACP agents, scanners, **refusal shims** |
| `choregos-laya` | the decision model behind LiteLLM (`platform/decide`, ADR 0121) | CPU only, checkpoint baked in at a pinned commit, offline at run time (`HF_HUB_OFFLINE=1`) |

## Build locally

```bash
docker build -f docker/api.Dockerfile --target api    -t choregos-api:dev .
docker build -f docker/api.Dockerfile --target worker -t choregos-orchestrator:dev .
docker build -f docker/runner.Dockerfile              -t choregos-runner:dev .
docker build -f docker/laya.Dockerfile                -t choregos-laya:dev .
```

`--target` is not optional on `api.Dockerfile`: without it, Docker builds the last stage of the
file (`worker`).

## What the runner image **does not contain**

No credential, no `kubeconfig`, no cloud token. `kubectl`, `terraform`, `az`, `gcloud`,
`aws`, `docker` and `helm` exist, but return an explicit refusal and log it in the run. The Git
token is minted per run, injected in memory, never written.

## Base images: the rule

The bases follow the **LTS** lines: `python:3.12-slim-bookworm` and `node:24-bookworm-slim`
today. An odd Node version (23, 25, …) is *Current* — maintained for a few months, never
promoted — and has no place in a production image. An even version is *Current* too until its
promotion, in October of its release year: Node 26, released on 2026-05-05, only becomes LTS on
2026-10-28 (official calendar: `nodejs/Release`, `schedule.json`). Dependabot will propose these
bumps anyway, that is its job; the rule is here so that they are judged on a criterion and not on
the mood of the day.

Node 25 also removed corepack, which `web.Dockerfile` uses to enable pnpm: a base bump can
cost a rewrite, not just a line. Node 26 inherits that — its image no longer has `corepack`
(`corepack enable` exits with 127 there); moving to 26 will require installing pnpm another way.

## Signature and provenance

The images published on `main` are signed with cosign (keyless, GitHub OIDC) and come with an
SBOM. Kyverno refuses, in the cluster, any unsigned image or one referenced by a moving tag (see
`infra/policies/images.yaml`).
