# Revue produit — 2026-10-07

Vincent a parcouru la console du locataire dev (chart 0.16.3) et l'a trouvée « peu claire à
l'usage ». Cette revue dit pourquoi, en partant du code, et ce que le flux S21 corrige. Elle est
critique à dessein : un produit qu'on montre à des clients doit tenir face à un utilisateur qui ne
l'a pas écrit.

## Ce qui tient

Le moteur est solide : workflows durables et rejouables (Temporal, historiques archivés), actions
gouvernées clé par clé avec compensation, politique qui ne fait que resserrer, coûts mesurés au
registre, exécution prouvée sur le dev (27/09 : modèle et outil réels, ticket traversé jusqu'à
`fini`). La règle « rien n'est ✅ sans un test qui échoue en son absence » est tenue.

## Ce qui ne tient pas, du plus grave au moins grave

1. **Une partie de la gouvernance annoncée n'est que déclarée.** Mesuré dans le code le 07/10 :
   - l'approbation d'un train écrite DANS un workflow (`train: {approval: captain}`) n'est lue par
     personne : `_run_train` (`interpreter.py`) ne passe que l'environnement, `signal_train`
     envoie `labels: []`, et `ReleaseTrain._depart` ne lit l'approbation que dans la POLITIQUE du
     projet ;
   - `review.humans`, `policy.approvals.merge|prod` et `train.auto_sync` ne sont lus nulle part ;
   - le train ne prévient jamais le ticket qu'il a livré : le ticket attend un `cd.*` que rien
     n'envoie (les tests l'injectent à la main) — en production, il finit en `needs_human` sur
     délai.

   Pour un produit dont la promesse est « des garanties mesurées », c'est le point le plus grave :
   un utilisateur qui écrit « la mise en production attend un capitaine » dans un workflow croit
   être protégé. S21 le corrige (approbation par release, le train prévient le ticket).
2. **Trop de notions exposées, aux noms qui se recouvrent** : workflow, acteur, rôle, playbook,
   agent du registre, agent implicite, runtime/backend, skill, connecteur de projet, connecteur
   d'organisation, opération, catalogue d'outils, intégration, porte MCP, action, proposition,
   approbation, demande humaine, tâche, train, garantie, finding, mémoire. Aucun écran ne dit
   comment elles tiennent ensemble ; `/admin/connectors` montrait à plat un annuaire, un parc et
   l'agent d'un fournisseur servi en MCP.
3. **Le cas d'usage principal (le logiciel) tourne sur des agents invisibles.** Les workflows
   livrés nomment des rôles, pas des agents du registre : pas de mesures, pas de budget par agent,
   pas de versions, pas de catalogue. `/agents` ne montrait que les deux coordinateurs RH.
4. **Pas de « dix premières minutes ».** Un projet neuf ne tourne pas tant que ses connecteurs et
   ses groupes humains n'existent pas, et rien ne le dit — alors que
   `GET /projects/{id}/requirements` le calcule déjà.
5. **Des étapes humaines adressées à des groupes que personne ne vérifie** (`product-owners`,
   `release-captains`, écrits en dur dans les gabarits). Un groupe inconnu de l'IdP, c'est un
   ticket qui attend pour toujours, en silence. Et aucune page ne dit « ce qui m'attend, moi ».
6. **Deux systèmes d'outils parallèles** : `config.tools` (catalogue) à côté des connecteurs
   d'organisation et de leur politique par opération — l'obstacle n° 11 du 27/09 a coûté des
   heures. Un connecteur de projet par sorte (`UniqueConstraint`) contredit l'ADR 0034.
7. **L'écriture d'un workflow, valeur centrale du produit, est fragile** : éditeur YAML cassé
   (Monaco téléchargé depuis un CDN que la CSP bloquait), carte illisible au-delà de dix états,
   deux chemins d'édition qui s'écrasaient sans le dire, acteurs non modifiables depuis la carte.
8. **Langue mêlée et vocabulaire d'initié** : du français dans une interface anglaise (noms
   d'états, étiquettes de la carte, messages du validateur et de l'API) ; tout en minuscules ;
   « born closed », « by reference », « acts as » sans aide ; codes d'erreur bruts
   (`workflow.effet_implicite`).
9. **Il faut venir dans la console.** Les équipes vivent dans GitHub, Jira et Slack ; les
   commandes `/choregos …` sont analysées mais ne déclenchent rien (S3-03 🟡), et il n'y a pas de
   courriel.
10. **La qualité des agents sur le dev** : opencode et les profils `cheap`/`standard` de la
    plateforme seulement. Un vrai ticket de développement sur un petit modèle donne un résultat
    faible : en démonstration, c'est un risque de crédibilité plus grand que n'importe quel défaut
    d'écran.
11. **Une démo faite à la main** : le projet `billing` du dev a été créé à la main ; rien ne
    recrée une organisation de démonstration cohérente.

## Ce que le flux S21 corrige

| Point | Stories |
|:--|:--|
| 1 — gouvernance déclarée | S21-21 (le train prévient le ticket, approbation par release, ADR 0041) |
| 2 — notions | S21-03 (menu, glossaire), S21-04 (connecteurs en sections) |
| 3 — agents invisibles | S21-15 à S21-19 (catalogue d'agents, clients externes, gabarits qui nomment des agents ; ADR 0040) |
| 7 — écriture d'un workflow | S21-01, S21-05 à S21-07 (grilles, éditeur, brouillon unique, carte à plat ; ADR 0038) |
| 8 — langue | S21-02, S21-09 à S21-14 (ADR 0039) |
| projet de développement | S21-20 à S21-25 (gabarit `github-software-delivery` : `dev-simple`, `study`, `dev-complex`) |

## Proposé ensuite, dans l'ordre

- **P1 — usage réel** : une liste de mise en route par projet (depuis `requirements` : dépôt, CI,
  CD, runtime, passerelle, groupes) ; une page « mon travail » (tout ce qui m'attend, tous projets
  confondus) ; les groupes humains vérifiés à la publication (inconnu ou vide : avertissement) ;
  `make demo-dev`, qui recrée l'organisation de démonstration en anglais.
- **P2 — adoption** : un seul système d'outils (`config.tools` déprécié au profit de la politique
  par opération) et plusieurs connecteurs par sorte ; `/choregos answer|retry` depuis GitHub et
  Jira (les décisions restent dans la console, ADR 0030) ; le courriel ; la politique en
  formulaire ; la vraie chaîne Argo CD (la PR GitOps qu'ouvre `promote` n'est fusionnée par
  personne) ; un profil `strong` sur la passerelle du dev (geste plateforme).
- **P3 — écriture** : une passe sur les textes (majuscules de phrase, glossaire complet, erreurs
  lisibles).
