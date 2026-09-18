# Ecphoria a perdu son quorum Raft

## Reconnaître

- L'alerte de quorum, ou `choregos_context_pack_empty_total` qui grimpe.
- Les runs continuent : **c'est normal et voulu** — sans mémoire, le context pack est vide.

## Comprendre

Ecphoria tourne en StatefulSet de trois répliques avec Raft. Un nœud perdu est toléré ;
deux, le quorum tombe et les écritures s'arrêtent.

```bash
kubectl -n choregos-memory get pods -l app.kubernetes.io/name=ecphoria
kubectl -n choregos-memory logs ecphoria-0 | grep -i raft
```

## Agir

1. **Un nœud perdu** : le laisser revenir. Si son PVC est corrompu, le supprimer :
   ```bash
   kubectl -n choregos-memory delete pvc data-ecphoria-2
   kubectl -n choregos-memory delete pod ecphoria-2
   ```
   Le nœud se resynchronise depuis le leader.
2. **Quorum perdu (deux nœuds)** : restaurer depuis la sauvegarde quotidienne.
   ```bash
   kubectl -n choregos-memory create job --from=cronjob/ecphoria-restore restore-$(date +%s)
   ```
3. **En attendant** : rien à faire côté plateforme. Le circuit-breaker de l'adaptateur rend
   des packs vides en moins de 300 ms, et les stages tournent sans mémoire.

## Vérifier

```bash
curl -s http://ecphoria.choregos-memory:8432/health | jq
choregos items show <ticket>   # les runs suivants ont de nouveau du contexte
```

## Ce qu'il ne faut pas faire

- Basculer sur `pgvector` dans la panique : c'est une décision de configuration réfléchie
  (ADR-0007), pas un geste d'urgence. Les faits écrits dans Ecphoria ne seraient pas là.
