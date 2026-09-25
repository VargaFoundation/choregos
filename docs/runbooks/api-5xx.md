# The API returns 5xx

## How to know it is this

The `ChoregosApiErreurs5xx` alert (more than 1 % of 5xx over 10 minutes), or the front
showing errors on every page.

## Understand

```bash
kubectl -n choregos-system logs deploy/choregos-api --tail=200 | jq 'select(.level=="error")'
kubectl -n choregos-system get pods -l app.kubernetes.io/component=api
curl -s https://api.<domain>/readyz
```

Three causes cover almost every case:

| Symptom in the logs | Cause | Action |
| :-- | :-- | :-- |
| `connection refused` to Postgres | database down or saturated | see `restauration-postgres.md`, check PgBouncer |
| `temporal` / `RPCError` | Temporal frontend unreachable | check `choregos-temporal`, see `montee-temporal.md` |
| `alembic` / unknown column | migration not applied | rerun the migrations Job |

## Act

1. **Check readiness** first: `/readyz` says whether the database and Temporal answer.
2. **Migration behind** (after a deployment):
   ```bash
   kubectl -n choregos-system get jobs | grep migrations
   kubectl -n choregos-system logs job/choregos-migrations-<revision>
   ```
   Migrations are N-1 compatible: rolling back to the previous version is safe.
3. **Saturation**: look at the HPA and at Postgres connections. Each API replica and each
   worker holds at most `pool.size + pool.maxOverflow` connections (`docs/deployment.md`,
   *Operating it*).
   ```bash
   kubectl -n choregos-system get hpa choregos-api
   kubectl -n choregos-data exec -it choregos-pg-1 -- psql -c \
     "select count(*), state from pg_stat_activity group by state"
   ```

## Check it is fixed

- The 5xx rate falls back under 1 % over 10 minutes.
- `choregos whoami` answers.
- Webhooks pass again: `kubectl logs ... | grep webhooks` shows 202s.

## Side effect worth knowing

An unavailable API **loses no work**: Temporal workflows carry on, GitHub redelivers its
webhooks, and the fallback polling catches moved cards within two minutes.
