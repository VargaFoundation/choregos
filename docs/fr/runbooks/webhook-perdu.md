# Un ticket `agent-ready` n'a pas démarré

## Comment savoir que c'est ça

L'étiquette `agent-ready` est posée sur le ticket depuis plusieurs minutes, et :

- le ticket n'apparaît pas dans `/p/{slug}` ni dans `GET /projects/{id}/work-items` ;
- aucun commentaire de suivi Choregos n'a été écrit sur le ticket ;
- côté GitHub, *Settings → Webhooks → Recent Deliveries* montre une livraison en rouge
  (401, 5xx, timeout) — ou aucune livraison du tout.

Si le ticket **existe** dans Choregos mais reste en `inbox`, ce n'est pas ce runbook :
voir [run-bloque.md](run-bloque.md).

## Ce qui devrait se passer tout seul

Le webhook est le chemin nominal ; le rattrapage est le filet. Une boucle
`TrackerReconciliation` par projet relit les candidats du tracker toutes les
`CHOREGOS_RECONCILE_INTERVAL_SECONDS` secondes (60 par défaut) et crée ce qui manque.
**Attendre une minute avant d'agir** règle la plupart des cas.

```bash
temporal workflow query --workflow-id reconcile-<slug> --type status
# {"passes": 412, "last": {"candidates": 3, "created": [], "started": []}, "stopped": false}
```

`passes` doit augmenter. `last.candidates` est ce que le tracker rend : s'il vaut 0 alors
que l'étiquette est posée, le problème est dans le tracker ou le connecteur, pas ici.

## Agir

**1. Forcer un rattrapage immédiat**

```bash
temporal workflow signal --workflow-id reconcile-<slug> --name reconcile_now --input '{}'
```

**2. La boucle n'existe pas ou s'est arrêtée** (`stopped: true`, ou workflow introuvable) :

```bash
kubectl -n choregos rollout restart deploy/choregos-orchestrator
```

Le worker redémarre les boucles des projets actifs au démarrage. Le démarrage est
idempotent : il ne crée pas de seconde boucle pour un projet qui en a déjà une.

**3. La boucle tourne mais `candidates` reste vide** — le connecteur ne voit pas le ticket :

```bash
choregos connectors test --project <slug> --kind tracker
```

Vérifier que l'étiquette est exactement `agent-ready`, que l'issue est ouverte, et que
l'App GitHub a accès au dépôt. Une clé privée expirée se voit dans le runbook
[rotation-secrets.md](rotation-secrets.md).

**4. Réparer la cause côté webhook** : réémettre les livraisons rouges depuis GitHub
(*Redeliver*), et vérifier le secret partagé si elles repartent en 401.

## Vérifier

- `GET /projects/{id}/work-items` liste le ticket ;
- le ticket porte un commentaire de suivi Choregos ;
- la livraison rejouée depuis GitHub répond 202.

## Ce qu'il ne faut pas faire

Créer le ticket à la main dans la base. L'identifiant du workflow est dérivé de la clé du
ticket : un ticket inséré à la main sans `temporal_wf_id` sera de toute façon repris par le
rattrapage, et deux insertions concurrentes laissent une ligne orpheline à nettoyer.
