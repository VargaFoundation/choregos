# Perte d'un nœud (éviction spot, panne matérielle)

## Reconnaître

Un nœud passe `NotReady` et n'en revient pas. Les pods qu'il portait restent affichés `Running`
pendant plusieurs minutes — ce n'est pas un bug d'affichage : **Kubernetes ne les évince pas tout
de suite**, et tant qu'il ne le fait pas, le travail ne repart nulle part.

```bash
kubectl get nodes
kubectl get pods -A -o wide --field-selector spec.nodeName=<nœud>
```

## Les trois instants qui comptent

Mesurés sur cluster (`tests/cluster/test_node_loss.py`), nœud arrêté brutalement :

| | Défauts Kubernetes | Avec `unreachableTolerationSeconds: 20` |
| :-- | --: | --: |
| Nœud perdu → `NotReady` | 52–57 s | idem (c'est le `node-monitor-grace-period`) |
| Nœud perdu → worker reparti ailleurs | **354–359 s** | **74 s** |

Les cinq minutes du milieu sont la tolérance par défaut d'une pod à
`node.kubernetes.io/unreachable`. Elle est prévue pour des charges dont le remplacement coûte cher
à démarrer — un worker Choregos n'en fait pas partie : il est interchangeable et Temporal reprend
son travail là où il en était.

Le chart met donc `choregos-orchestrator.unreachableTolerationSeconds: 20`. Pour revenir au
comportement standard de Kubernetes : `0`.

## Pendant la panne

1. **Vérifier que l'API n'est pas touchée.** Elle a des `topologySpreadConstraints` : si tout est
   tombé, le problème est plus large qu'un nœud.
   ```bash
   kubectl -n choregos-system get pods -l app.kubernetes.io/component=api -o wide
   ```
2. **Ne rien forcer pendant les 20 premières secondes.** Un nœud qui revient (redémarrage réseau,
   kubelet relancé) reprend ses pods, et un `delete --force` aurait créé des doublons pour rien.
3. **Après l'éviction**, vérifier que les runs portés par le nœud ont bien repris :
   ```bash
   kubectl -n choregos-system logs -l choregos/queue=executor --tail=50 | grep -i resume
   ```
   Temporal rejoue l'activité ; l'idempotence de `start` fait que le job déjà lancé n'est pas
   relancé une seconde fois — c'est ce que vérifie `test_start_rejoue_ne_double_pas_l_execution`
   pour ACA et l'équivalent Kubernetes côté `k8s_job`.

## Si un run reste bloqué

Un run dont le pod a disparu sans que Temporal le remarque se débloque en relançant l'activité :

```bash
# Identifier le run
kubectl -n choregos-system exec deploy/choregos-api -- choregos runs list --state running
# Le relancer (idempotent — pas de double exécution ni de double facturation)
kubectl -n choregos-system exec deploy/choregos-api -- choregos runs resume <run-id>
```

## Ce que ceci ne couvre pas

- **Le préavis d'éviction spot.** Azure et AWS préviennent ~30 secondes avant de reprendre une
  machine. La plateforme ne l'écoute pas : elle subit la perte comme une panne sèche. L'écouter
  permettrait de vider le nœud avant qu'il ne parte — c'est une amélioration, pas un correctif.
- **La perte simultanée de plusieurs nœuds.** Avec des workers sur deux nœuds et les deux perdus,
  il n'y a nulle part où replacer : c'est un problème de capacité, pas de tolérance.
- **Un nœud qui revient après l'éviction.** Ses anciens pods sont supprimés par le kubelet au
  redémarrage ; rien à faire à la main.
