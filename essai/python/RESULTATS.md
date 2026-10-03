# Essai, élément TRV-009 — Python 3.12 ou 3.13 ? Résultats du 2026-10-03

**Question** (Q14 du cahier des charges) : le moteur low-code repris de graal exige Python ≥ 3.13,
Choregos est figé sur `>=3.12,<3.13`. Faut-il un service séparé pour le moteur, ou une seule version ?

**Réponse : une seule version, 3.13, pour tout le monorepo.**

| Suite | Python 3.12 | Python 3.13 |
|---|---|---|
| Moteur low-code de graal (`lowcode/`, tests unitaires, hors Java) | 4 906 réussis, 82 sautés (après avoir relâché `requires-python`) | 4 906 réussis, 82 sautés (73 s) |
| Choregos (suite rapide, `-m "not slow"`) | 918 réussis, 18 sautés | 918 réussis, 18 sautés (dépendances résolues à neuf) |
| Choregos, `mypy --strict` (214 fichiers) | sans erreur | sans erreur (`python_version = "3.13"`) |

**Méthode.**
- Le moteur a été extrait de graal (`git archive origin/main lowcode`, révision `35f6eb2f`) dans un
  répertoire temporaire, **hors de ce dépôt public** : aucun code de graal n'est versé ici avant l'audit
  des droits (TRV-040).
- Choregos a été testé sous 3.13 dans un worktree jetable, avec `requires-python = ">=3.12,<3.14"`.

**Ce qu'on en tire.**
- La contrainte `>=3.13` du moteur n'est pas une nécessité technique pour ses tests unitaires ;
  Choregos passe sous 3.13 sans modification de code.
- Recommandation : **Python 3.13 partout**, adopté par une PR de structure en phase 1
  (`requires-python`, CI, images) ; le moteur entre tel quel, sans service séparé.
- Non vérifié : les tests e2e du moteur (services externes) et les tests lents ou de cluster de
  Choregos sous 3.13.
