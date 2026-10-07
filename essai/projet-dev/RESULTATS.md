# Résultats — le projet de développement sur le dev

## 2026-10-07 — préparé, pas encore joué

**Prêt :**
- le dépôt bac à sable `DiametralGroup/choregos-sandbox-dev` (privé) : `invoices` (un `compute_total`
  qui ignore les avoirs — le bogue du premier ticket), trois tests, `python -m invoices.smoke`, un ADR
  0000 au format MADR 4, GitHub Actions — **vert** sur `main` au premier push ;
- `scenario_dev.py` (préparer, exigences, ouvrir, suivre, vérifier).

**Pas joué**, parce que le dev ne porte pas encore ce qu'il faut :
- les PR #276 → #289 (S21-19 à S21-24) ne sont pas fusionnées, aucune release ne les porte, et
  `choregos-deploy` ne les vend pas ;
- l'App GitHub de Choregos n'est pas installée sur le bac à sable ;
- les groupes `maintainers`, `product-owners`, `release-captains`, `architects` ne sont pas vérifiés
  dans le fournisseur d'identité.

Rien n'est donc mesuré sur le locataire : ni états, ni coûts, ni PR fusionnée. Cette page sera
complétée au premier passage.
