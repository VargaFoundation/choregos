# Développer sur Choregos

## Trois niveaux, du plus léger au plus proche de la production

| Niveau | Commande | Ce que ça donne | Ce qu'il faut |
| :-- | :-- | :-- | :-- |
| démonstration | `make demo` | la chaîne complète en mémoire, un ticket jusqu'à la prod | rien |
| services locaux | `make compose-up` puis `make api` / `make worker` | l'API et les workers réels, connecteurs simulés | Docker |
| cluster | `make dev-up` | kind + Tekton + Argo + Tilt, rechargement à chaud | kind, kubectl, helm, tilt |

## Démonstration (aucune dépendance)

```bash
make demo
```

Un ticket entre, un agent simulé cadre, un humain valide, l'agent implémente, les gates
vérifient, la PR s'ouvre, le train déploie. La sortie montre le commentaire écrit dans le
ticket, les coûts par étape et le finding transformé en ticket lié.

## Services locaux

```bash
make compose-up                          # Postgres, Temporal, LiteLLM, Keycloak, MinIO
CHOREGOS_FAKES=1 make api                # API sur :8000
CHOREGOS_FAKES=1 make worker             # workers Temporal
make dev-seed                            # organisation, projet, 10 tickets, un lot
pnpm -C apps/web dev                     # front sur :3000
```

Comptes de développement (Keycloak, realm `choregos`) : `augustin` / `choregos`
(propriétaire), `marie` / `choregos` (release captain). En local, `/auth/login?as=<email>`
ouvre une session sans passer par l'IdP — uniquement quand `dev_login_enabled` est vrai.

## Cluster de développement

```bash
make dev-up      # kind + dépendances + Tilt
make dev-seed
make dev-down    # tout détruire, volumes compris
```

Tilt recharge à chaud l'API, le front, les workers, le runner et le sidecar. L'interface
Tilt expose aussi trois boutons : *seed*, *démo hors ligne*, *tests*.

## Le front sans l'API

```bash
NEXT_PUBLIC_API_MODE=mock pnpm -C apps/web dev
```

Des fixtures cohérentes racontent un projet vivant : un ticket en cours, une validation en
attente, un lot prêt à partir, un finding à trier.

## Écrire un workflow

```bash
choregos workflow templates                    # les trois modèles livrés
choregos workflow validate .choregos/workflow.yaml
choregos workflow show .choregos/workflow.yaml --mermaid
```

La validation est **locale et hors ligne** : le même code que l'orchestrateur, donc ce que
vous voyez est ce qui s'appliquera. Les erreurs portent la ligne et la colonne.

## Déboguer un run

```bash
choregos items list <projet>
choregos runs list <ticket-id>
choregos runs tail <run-id>      # journal ACP en direct
choregos runs diff <run-id>      # diff annoté : ce qui est hors périmètre est marqué
```

## Pièges connus

- **Le serveur de test Temporal se télécharge** au premier `pytest` sur les workflows.
  Prévoir une connexion, ou lancer `uv run pytest packages` (aucun Temporal requis).
- **`CHOREGOS_FAKES=1` change tout** : en mode fakes, aucun appel réel n'est fait, et les
  coûts affichés sont simulés. C'est très bien pour développer, jamais pour juger un modèle.
- **Les migrations sont compatibles N-1** : revenir en arrière est sûr, mais un `downgrade`
  d'une migration qui supprime une colonne perd des données.
