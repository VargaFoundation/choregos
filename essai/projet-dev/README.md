# Le projet de développement sur le dev

`scenario_dev.py` joue le gabarit `github-software-delivery` sur le locataire dev, avec un vrai dépôt
([DiametralGroup/choregos-sandbox-dev](https://github.com/DiametralGroup/choregos-sandbox-dev) : une
petite bibliothèque de facturation, ses tests, GitHub Actions), de vrais agents du catalogue et de
vraies décisions. Le même parcours, sur des faux et en temps accéléré, est
`apps/orchestrator/tests/test_gabarit_livraison_logicielle.py`.

## Ce qu'il faut au dev

- Une version de la plateforme qui porte S21-21 à S21-24 (le train qui prévient le ticket,
  l'approbation par workflow, le gabarit, le `cd: demo`), vendue dans `choregos-deploy`.
- **Les faux servis par un pod** (`demoFakes.enabled: true`, déjà vrai pour le scénario RH) : le
  train promeut dans `/cd/mcp`.
- **L'App GitHub de Choregos installée sur le dépôt bac à sable** — geste de la console GitHub ;
  son identifiant d'installation va dans `ESSAI_INSTALLATION`.
- **Qui décide** : les groupes `maintainers`, `product-owners`, `release-captains`, `architects`
  disent à qui s'adresse une étape humaine ; trancher demande seulement le rôle `developer` (ou plus)
  sur le projet, et le départ en production le rôle `release_captain`. Un administrateur peut tout.
- La protection de `main` sur le bac à sable : checks requis (`ci / test`), **aucune revue requise**
  (la plateforme fusionne, les personnes décident dans Choregos).

## Le déroulé

1. `preparer` : le projet `dev` né du gabarit, ses connecteurs (`github-issues`, `github` sans file
   de fusion, `cd: demo` vers les faux), le backend et les profils que le dev sert, le fait
   `smoke_ok` (`python -m invoices.smoke`) ; puis `exigences`, qui sort en erreur s'il manque un
   connecteur.
2. `ouvrir bug`, `ouvrir adr`, `ouvrir feature` : une issue étiquetée dans le bac à sable — le
   webhook la fait naître dans le bon workflow.
3. `suivre <id>` : l'état, et l'adresse où décider. **Les décisions se prennent dans la console**,
   par une personne ré-authentifiée : la spec et la PR de la fonctionnalité, la décision sur l'ADR ;
   le départ du train de prod de la fonctionnalité, dans la page du train.
4. `verifier <id>` : chaque run (son agent du catalogue, sa transition, son coût), chaque décision,
   la PR. Le code de sortie dit si le ticket a fini.

Ce qu'on attend : le bogue arrive à `verified` sans une décision humaine ; l'étude s'arrête à
`awaiting_decision`, puis, approuvée, fusionne `docs/adr/0001-*.md` en `accepted` sans train ; la
fonctionnalité attend trois fois une personne, passe par staging, puis attend son capitaine.

Le jeton d'API (`CHOREGOS_DEV_TOKEN`) ne s'écrit nulle part : il vient de l'environnement.
