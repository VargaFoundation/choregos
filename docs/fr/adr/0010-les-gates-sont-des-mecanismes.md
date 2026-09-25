# ADR-0010 — Une garantie est un mécanisme, jamais un prompt

- **État** : acceptée
- **Concerne** : S1, S2, S3

## Contexte

Il est tentant d'écrire dans un prompt « ne modifie pas les fichiers hors périmètre » et de
considérer le problème réglé. Un modèle suit une consigne la plupart du temps ; « la plupart
du temps » n'est pas une garantie.

## Décision

Chaque propriété qu'on veut garantir a un **mécanisme** qui la vérifie, indépendant du
prompt :

| Ce qu'on veut | Le mécanisme |
| :-- | :-- |
| l'agent reste dans son périmètre | permission ACP refusée, **puis vérification du diff et revert**, **puis** gate `scope_respected` |
| les tests passent vraiment | le runner **exécute** les commandes du dépôt et écrase les preuves déclarées |
| pas de secret commité | `no_secrets` sur le diff + gitleaks en CI |
| pas de dépassement de budget | plafond dur sur la clé virtuelle du run |
| la prod n'est atteinte que par le train | règle de validation du DSL (`prod.requires_train`) |
| l'agent n'a pas d'accès cloud | aucun credential + NetworkPolicy + shims qui refusent |

Le prompt sert à obtenir un bon comportement ; le mécanisme sert à ce qu'un mauvais
comportement ne passe pas.

## Conséquences

- Un agent qui tente une écriture hors périmètre reçoit un refus **motivé**, qui lui
  rappelle les outils légitimes (`report_finding`, `request_scope_change`).
- Les preuves d'une étape sont celles que le runner a mesurées, pas celles que l'agent a
  écrites — même quand l'agent est honnête.
- Chaque gate a un test vert et un test rouge.
