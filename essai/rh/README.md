# Le scénario RH sur le dev

`scenario_rh.py` joue le gabarit `joiners-leavers` sur le locataire dev, avec un vrai agent et de
vraies décisions. Le même scénario, en temps accéléré et sur des faux en mémoire, est
`tests/e2e/test_m6_rh.py` : celui-ci montre la même chose **déployée**.

## Ce qu'il faut au dev

- Une version de la plateforme qui porte le lot 6 (S20-01 à S20-07).
- **Les faux servis par un seul pod** : `demoFakes.enabled: true` dans les valeurs du dev
  (choregos-deploy). L'API tourne en deux répliques et l'orchestrateur à part ; des faux en
  mémoire leur montreraient autant d'annuaires et de parcs qu'il y a de processus. Le chart le
  refuse en staging et en prod.
- Facultatif : un jeton pour les faux (`demoFakes.tokenSecret`), posé aussi dans l'environnement
  de l'API et de l'orchestrateur ; on passe alors sa référence au script (`ESSAI_JETON_REF`).

## Le déroulé

1. `preparer` : les cinq connecteurs de l'organisation (`annuaire` → le faux Graph, `parc`,
   `transporteur`, `lecteurs`, `fournisseur` → les faux servis), les outils du fournisseur
   découverts — fermés —, ce que l'organisation permet sans décision humaine, et le projet `rh`.
2. `arrivee` : la demande, par la porte MCP, comme le Claude d'une RH. La date d'arrivée est hier :
   le dev ne saute pas le temps, J-10 et J-7 sont passés, J+1 est aujourd'hui. `arrivee --dans 30`
   montre au contraire l'attente — et `deplacer <ticket> <date>` la défait : le minuteur se réarme.
3. `suivre <ticket>` : ce qu'attend le ticket, et l'adresse où le décider. **Les décisions se
   prennent dans la console**, par une personne ré-authentifiée : la validation du plan, le groupe
   sensible, la commande du poste ; la tâche « badge » s'y fait aussi, et s'y atteste.
4. `depart <ticket d'arrivée>` : le départ de la même personne, aujourd'hui (J0).
5. `verifier <ticket>` : chaque action, qui l'a décidée, son journal effet par effet, et ce qui a été
   attesté — le dossier de preuves. Le code de sortie dit si tout a réussi.

Le jeton d'API (`CHOREGOS_DEV_TOKEN`) ne s'écrit nulle part : il vient de l'environnement.
