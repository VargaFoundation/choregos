# Un train est gelé et rien ne part

## Reconnaître

- L'alerte `ChoregosTrainGele` se déclenche (gelé depuis plus de 2 h).
- Le front `/p/<projet>/trains` affiche **Train gelé** avec un motif.
- Des tickets s'accumulent dans `pending_items` sans départ.

## Comprendre

Un train se gèle de deux façons : **un humain** l'a gelé (le motif est affiché), ou un
**rollback** l'a gelé automatiquement (`freeze_on_rollback: true`). Le second cas est le
plus fréquent et le plus important : la plateforme refuse de redéployer par réflexe après
un incident.

```bash
choregos trains status <projet> --env prod
# ou directement au workflow :
temporal workflow query --workflow-id train-<projet>-prod --name status_query
```

## Agir

1. **Lire le motif.** S'il vient d'un rollback, trouver la release :
   ```bash
   choregos trains status <projet> --env prod
   curl -s "$API/api/v1/projects/<projet>/releases?env=prod" | jq '.items[0]'
   ```
   Le champ `verdict.reason` dit ce qui a échoué (analyse canary, smoke, SLO).
2. **Corriger la cause**, pas le symptôme. Un finding `critical` a été créé
   automatiquement : il porte la preuve.
3. **Dégeler** quand le correctif est en route :
   ```bash
   choregos trains unfreeze <projet> --env prod
   ```
4. Si un correctif doit partir tout de suite, utiliser la **voie express** : poser le label
   `hotfix` sur le ticket. Elle réduit le soak et saute le cron — mais **pas** le gel :
   il faut dégeler d'abord. C'est voulu.

## Vérifier

- `choregos trains status <projet> --env prod` : `frozen: false`, un `next_departure`.
- Le lot repart : la release passe `departing` → `staging` → `done`.
- L'alerte se résout d'elle-même.

## Ne pas faire

- Dégeler sans avoir compris le rollback : le train repartira sur la même cause.
- Déployer à la main pour « débloquer » : le troisième verrou (fenêtres Argo, Environment
  GitHub) vous en empêchera, et c'est heureux.
