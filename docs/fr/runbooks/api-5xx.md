# L'API renvoie des 5xx

## Reconnaître

L'alerte `ChoregosApiErreurs5xx` (plus de 1 % de 5xx sur 10 minutes), ou le front qui
affiche des erreurs sur toutes les pages.

## Comprendre

```bash
kubectl -n choregos-system logs deploy/choregos-api --tail=200 | jq 'select(.level=="error")'
kubectl -n choregos-system get pods -l app.kubernetes.io/component=api
curl -s https://api.<domaine>/readyz
```

Trois causes couvrent presque tous les cas :

| Symptôme dans les logs | Cause | Action |
| :-- | :-- | :-- |
| `connection refused` vers Postgres | base indisponible ou saturée | voir `restauration-postgres.md`, vérifier PgBouncer |
| `temporal` / `RPCError` | frontend Temporal injoignable | vérifier `choregos-temporal`, voir `montee-temporal.md` |
| `alembic` / colonne inconnue | migration non appliquée | rejouer le Job de migrations |

## Agir

1. **Vérifier la disponibilité** avant tout : `/readyz` dit si la base répond.
2. **Migration en retard** (après un déploiement) :
   ```bash
   kubectl -n choregos-system get jobs | grep migrations
   kubectl -n choregos-system logs job/choregos-migrations-<révision>
   ```
   Les migrations sont compatibles N-1 : revenir à la version précédente est sûr.
3. **Saturation** : regarder le HPA et les connexions Postgres.
   ```bash
   kubectl -n choregos-system get hpa choregos-api
   kubectl -n choregos-data exec -it choregos-pg-1 -- psql -c \
     "select count(*), state from pg_stat_activity group by state"
   ```

## Vérifier

- Le taux de 5xx retombe sous 1 % sur 10 minutes.
- `choregos whoami` répond.
- Les webhooks repassent : `kubectl logs ... | grep webhooks` montre des 202.

## Effet de bord à connaître

Une API indisponible **ne perd pas de travail** : les workflows Temporal continuent, les
webhooks GitHub sont rejoués par GitHub, et le polling de secours rattrape les cartes
déplacées en moins de deux minutes.
