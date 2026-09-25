# ADR-0006 — Trois verrous indépendants protègent la production

- **État** : acceptée
- **Concerne** : S9, S3, S7

## Contexte

Des agents produisent des PR à un rythme qu'aucune revue humaine ne suit. Le risque n'est
pas qu'un agent écrive du mauvais code — la CI l'attrape — mais que dix changements
arrivent en production en même temps, sans que personne ne sache lequel a cassé quoi.

## Décision

Trois verrous, indépendants, qui ne tombent pas ensemble :

1. **La merge queue** (GitHub) : `main` reste intégré et vert, une PR à la fois.
2. **Le release train** (Temporal, singleton par projet × environnement) : un seul
   déploiement en cours, des lots, une cadence, des fenêtres, un soak, une approbation,
   un canary, un rollback, un gel.
3. **Les garde-fous déclaratifs** : *sync windows* Argo CD, `GitHub Environment production`
   avec approbateurs requis, lock Atlantis pour Terraform.

Le troisième verrou existe pour une raison précise : **si Choregos tombe, personne ne
déploie hors des règles**. La plateforme n'est pas le seul rempart.

## Conséquences

- Un rollback gèle le train (`freeze_on_rollback`) : on ne réessaie pas par réflexe.
- Un gel exige un motif — un train gelé sans raison est un incident silencieux.
- Un départ demandé par un humain passe outre la fenêtre et le cron (c'est un acte tracé),
  mais **jamais** outre un gel.

## Alternatives écartées

- **Déploiement continu sur merge** : ingérable au rythme d'une flotte d'agents.
- **Approbation manuelle de chaque PR** : ramène le goulot humain qu'on cherche à éviter.
