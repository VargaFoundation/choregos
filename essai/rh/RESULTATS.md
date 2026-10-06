# Le scénario RH sur le dev — résultats du 2026-10-06

**Question** : le gabarit `joiners-leavers` tient-il sur le locataire dev déployé, en 0.16.1, comme
il tient dans l'e2e à temps accéléré (`tests/e2e/test_m6_rh.py`) ?

**Réponse, jusqu'à la première décision humaine : oui**, après un correctif du script. La suite —
la validation RH, le groupe sensible, la commande du poste, la tâche « badge » — attend des
personnes, dans la console : le script ne décide rien.

## Ce qui a été joué

1. **`preparer`** : les cinq connecteurs de l'organisation (`annuaire` → le faux Microsoft Graph,
   `parc`, `transporteur`, `lecteurs`, `fournisseur` → les faux servis par le pod
   `choregos-demo-fakes`), les outils du fournisseur DÉCOUVERTS — la plateforme a joint le pod des
   faux depuis le cluster —, les politiques, et le projet `rh`. Né du gabarit : les workflows
   `onboarding` et `offboarding` (v1), le routage (`offboarding` sur étiquette), les agents
   `coordinateur-onboarding` et `coordinateur-offboarding`, les skills `procedure-onboarding` et
   `profils-d-acces`, et — le greffon de l'ontologie est actif sur le dev — le registre
   `rh-arrivees-departs` (six types d'objets, 28 outils MCP), installé par l'extension du gabarit
   (S20-09) sous PostgreSQL et sa RLS.
2. **`arrivee`** : la demande déposée par la porte MCP du projet, avec un jeton `mcp:write` créé puis
   révoqué, comme le ferait le Claude d'une RH.
3. **Le plan de l'agent** (`t-plan`, `coordinateur-onboarding@1`) : opencode, `platform/standard`,
   0,21 $ ; l'agent a CHARGÉ ses deux skills (journal ACP : `Loaded skill: procedure-onboarding`,
   `Loaded skill: profils-d-acces`), a cherché le compte dans l'annuaire par
   `choregos_annuaire__get_user` — le courtier, la clé du connecteur, le faux Graph —, et a rendu
   `plan_d_acces` (« Plan d'accès préparé pour l'arrivée de Léa Martin, sans écart détecté ») : la
   garantie `outputs_present` passe, le ticket attend la validation RH.

## Le défaut trouvé : un projet neuf part sur `claude-code`

Le premier passage (RH-1) a vu `t-plan` échouer deux fois, sans un appel au modèle (0 $, aucun
événement du runner, « Job has reached the specified backoff limit »), et le ticket finir
`a_revoir`. Un projet neuf reçoit `agent.default_backend: claude-code` et aucun profil de modèle ;
le dev fait tourner opencode derrière la passerelle de la plateforme, comme l'essai IT4IT le
configurait. `preparer` pose désormais le backend et le profil `standard` à chaque passage
(`ESSAI_BACKEND`, défaut `opencode` ; `ESSAI_MODELE`, défaut `platform/standard`) ; RH-2 est passé.

Ce que le défaut dit du produit : un gabarit livre des agents sans dire sur quoi ils tournent, et un
projet ne sait pas hériter du backend que son déploiement sait servir. La version d'un agent peut
nommer son backend ; un défaut par déploiement manque.

## Ce qui attend des personnes (console, ré-authentifié)

- la validation du plan d'accès par les RH (`t-validation`) ;
- puis, d'office : le compte et son groupe (J-10 passé) ;
- le groupe sensible : une validation ré-authentifiée ;
- la commande du poste à l'agent du fournisseur : une validation ;
- la tâche « badge » : l'UID saisi, la remise attestée ;
- le contrôle à J+1 ; puis `depart RH-2` et `verifier RH-2`.
