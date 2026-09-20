# Images de Choregos

| Image | Contenu | Particularité |
| :-- | :-- | :-- |
| `choregos-api` | API FastAPI + migrations Alembic | non-root, système de fichiers en lecture seule |
| `choregos-orchestrator` | workers Temporal | cible `worker` de `api.Dockerfile` — même build, même commit |
| `choregos-tools` | sidecar MCP | ne détient que le jeton du run |
| `choregos-web` | front Next.js | build de production, CSP stricte |
| `choregos-runner` | le workspace d'un agent | agents ACP épinglés, scanners, **shims de refus** |

## Construire localement

```bash
docker build -f docker/api.Dockerfile --target api    -t choregos-api:dev .
docker build -f docker/api.Dockerfile --target worker -t choregos-orchestrator:dev .
docker build -f docker/runner.Dockerfile              -t choregos-runner:dev .
```

`--target` n'est pas optionnel sur `api.Dockerfile` : sans lui, Docker construit la dernière
étape du fichier (`worker`).

## Ce que l'image du runner **ne contient pas**

Aucun credential, aucun `kubeconfig`, aucun jeton cloud. `kubectl`, `terraform`, `az`,
`gcloud`, `aws`, `docker` et `helm` existent, mais renvoient un refus explicite et le
journalisent dans le run. Le jeton Git est minté par run, injecté en mémoire, jamais écrit.

## Images de base : la règle

Les bases suivent les lignes **LTS** : `python:3.12-slim-bookworm` et `node:22-bookworm-slim`
aujourd'hui. Une version impaire de Node (23, 25, …) est *Current* — maintenue quelques mois,
jamais promue — et n'a rien à faire dans une image de production. Dependabot proposera quand
même ces montées, c'est son travail ; la règle est ici pour qu'elles soient jugées sur un
critère et non sur l'humeur du jour.

Node 25 a en prime retiré corepack, dont `web.Dockerfile` se sert pour activer pnpm : une
montée de base peut coûter une réécriture, pas seulement une ligne.

## Signature et provenance

Les images publiées sur `main` sont signées avec cosign (keyless, OIDC GitHub) et
accompagnées d'un SBOM. Kyverno refuse en cluster toute image non signée ou référencée
par tag mouvant (voir `infra/policies/images.yaml`).
