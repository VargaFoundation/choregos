# choregos-infra — le cluster en Git

Ce répertoire est la **seule source de vérité** du cluster : personne, ni humain ni API,
n'applique un manifeste à la main. Il est prévu pour vivre dans un dépôt séparé
(`VargaFoundation/choregos-infra`) ; il est livré ici pour que le déploiement soit
reproductible dès le premier jour.

```
bootstrap/   opérateurs et outillage (sync-wave 0–2) : cert-manager, ESO, Envoy Gateway,
             CNPG, Tekton, Argo Rollouts, Kyverno, monitoring, gVisor
platform/    l'umbrella `charts/choregos`, par environnement (sync-wave 3)
projects/    écrit par ProjectProvisioning ; un répertoire par projet (sync-wave 4)
policies/    Kyverno : images signées par digest, non-root, labels, quotas, RuntimeClass
```

## Installer

```bash
kubectl apply -f bootstrap/app-of-apps.yaml     # une seule fois, par un humain
argocd app sync bootstrap                        # puis Argo CD fait le reste
```

## Ordre de synchronisation

| Vague | Contenu | Pourquoi |
| --: | :-- | :-- |
| 0 | cert-manager, External Secrets, Kyverno | rien ne démarre sans secrets ni certificats |
| 1 | CNPG, Envoy Gateway, Tekton, Argo Rollouts, gVisor | les opérateurs avant leurs objets |
| 2 | monitoring (kube-prometheus-stack, Loki, Tempo, OTel) | pour voir le reste démarrer |
| 3 | `platform/` — l'umbrella Choregos | la plateforme elle-même |
| 4 | `projects/` — ApplicationSet par répertoire | les projets provisionnés |

## Secrets

Aucun secret n'est dans Git. Un unique `ClusterSecretStore` est créé à la main au
bootstrap ; tout le reste passe par des `ExternalSecret`. Rotations : clé d'App GitHub
90 j, `master_key` LiteLLM 90 j, clés JWT de l'API 30 j (deux clés), secrets de webhook 180 j.
