# Images de Choregos

| Image | Contenu | Particularité |
| :-- | :-- | :-- |
| `choregos-api` | API FastAPI + migrations Alembic | non-root, système de fichiers en lecture seule |
| `choregos-orchestrator` | workers Temporal | même socle que l'API |
| `choregos-tools` | sidecar MCP | ne détient que le jeton du run |
| `choregos-web` | front Next.js | build de production, CSP stricte |
| `choregos-runner` | le workspace d'un agent | agents ACP épinglés, scanners, **shims de refus** |

## Construire localement

```bash
docker build -f docker/api.Dockerfile   -t choregos-api:dev .
docker build -f docker/runner.Dockerfile -t choregos-runner:dev .
```

## Ce que l'image du runner **ne contient pas**

Aucun credential, aucun `kubeconfig`, aucun jeton cloud. `kubectl`, `terraform`, `az`,
`gcloud`, `aws`, `docker` et `helm` existent, mais renvoient un refus explicite et le
journalisent dans le run. Le jeton Git est minté par run, injecté en mémoire, jamais écrit.

## Signature et provenance

Les images publiées sur `main` sont signées avec cosign (keyless, OIDC GitHub) et
accompagnées d'un SBOM. Kyverno refuse en cluster toute image non signée ou référencée
par tag mouvant (voir `infra/policies/images.yaml`).
