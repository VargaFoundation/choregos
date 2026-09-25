# Restauration PostgreSQL (PITR)

## Reconnaître

Perte de données, corruption, ou suppression accidentelle. RPO visé : 5 minutes.
RTO visé : 30 minutes.

## Avant de restaurer

1. **Arrêter les écritures** : mettre l'API à zéro réplique et les workers en pause.
   ```bash
   kubectl -n choregos-system scale deploy/choregos-api --replicas=0
   kubectl -n choregos-system scale deploy/choregos-orchestrator-orchestrator --replicas=0
   ```
2. **Noter l'instant de restauration** (juste avant l'incident), en UTC.
3. **Vérifier qu'une sauvegarde couvre cet instant** :
   ```bash
   kubectl -n choregos-data get backups.postgresql.cnpg.io
   ```

## Restaurer

CloudNativePG restaure dans un **nouveau cluster** ; on ne restaure jamais par-dessus.

```yaml
apiVersion: postgresql.cnpg.io/v1
kind: Cluster
metadata:
  name: choregos-pg-restore
  namespace: choregos-data
spec:
  instances: 3
  bootstrap:
    recovery:
      source: choregos-pg
      recoveryTarget:
        targetTime: "2026-09-19 02:15:00+00"   # l'instant noté plus haut
  externalClusters:
    - name: choregos-pg
      barmanObjectStore:
        destinationPath: s3://choregos-backups/choregos-pg
        # `serverName` est le nom du cluster **d'origine**. Sans lui, CNPG cherche la
        # sauvegarde sous le nom du nouveau cluster et répond « no target backup found »,
        # avec un magasin d'objets pourtant plein. Vérifié en restaurant pour de vrai.
        serverName: choregos-pg
        s3Credentials:
          accessKeyId: { name: choregos-backup, key: access-key-id }
          secretAccessKey: { name: choregos-backup, key: secret-access-key }
```

```bash
kubectl apply -f restore.yaml
kubectl -n choregos-data wait --for=condition=Ready cluster/choregos-pg-restore --timeout=30m
```

## Basculer

1. Vérifier le contenu restauré **avant** de basculer :
   ```bash
   kubectl -n choregos-data exec -it choregos-pg-restore-1 -- psql choregos -c \
     "select count(*), max(created_at) from work_items"
   ```
2. Pointer le secret `choregos-db` sur le nouveau service (`choregos-pg-restore-rw`).
3. Remonter l'API et les workers, dans cet ordre.

## Vérifier

- `/readyz` répond, le front affiche les projets.
- Les workflows Temporal reprennent : leur état est dans Temporal, pas dans cette base.
- **Attention** : les runs en cours au moment du point de restauration seront rejoués ;
  l'idempotence (ADR-0008) évite le double coût.

## Test mensuel

Un `CronJob` de vérification restaure en staging et compare les compteurs. Un échec de ce
test est un incident : une sauvegarde non testée n'est pas une sauvegarde.
