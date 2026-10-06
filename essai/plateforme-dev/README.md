# Essai du socle sur le locataire dev

Ce que l'essai n'a pas pu prouver en local, éprouvé sur le locataire dev de Diametral
(`http://choregos.internal.dev.diametral.com`, derrière NetBird) :

- **un vrai agent** (opencode, modèle de la passerelle de la plateforme) choisit lui-même
  `finding_search` et `host_get`, puis propose `open_infra_pr` (éléments 3 et 4) ;
- **une vraie PR**, ouverte par la plateforme sur un dépôt de bac à sable, avec l'App GitHub du
  locataire (élément 5) ;
- **une vraie ré-authentification** par Keycloak : la décision porte l'`auth_time` de l'IdP
  (élément 5, et l'issue #153 éprouvée face à un vrai IdP).

## Ce qu'il faut sur le locataire

1. La version 0.13.0 de Choregos (le greffon y est livré, inactif).
2. Dans `choregos-deploy` :
   - le chart 0.13.0 vendoré ;
   - `global.extraEnv: [{name: CHOREGOS_ESSAI_ONTOLOGIE, value: "1"}]` pour activer le greffon ;
   - le playbook `analyse_infra.md` dans un ConfigMap désigné par `global.playbooks.configMap`.
3. Un **dépôt de bac à sable** où l'App GitHub du locataire est installée (contenus et PR en
   écriture). Les chemins permis par l'action sont `platform/**` et `gitops/**`.

## Le parcours

| Étape | Qui | Comment |
|---|---|---|
| 1 | Un humain | Se connecter à la console, créer un jeton d'API (`POST /api/v1/me/tokens`), le garder hors du dépôt |
| 2 | Le script | `scenario_dev.py preparer` : projet `essai-it4it`, connecteurs (`internal`, `github`), flux `essai-it4it`, ontologie IT4IT, deux hôtes par `register_host`, un rapport de constats, puis un ticket démarré |
| 3 | L'agent | Instruit le constat avec les outils générés et propose `open_infra_pr` ; le ticket passe à `propose` |
| 4 | Un humain | Se ré-authentifier (`/api/v1/auth/login?reauth=1`), puis valider la proposition (ci-dessous) |
| 5 | La plateforme | Écrit le fichier sur `choregos/<proposition>` et ouvre la PR ; la preuve la constate |
| 6 | Le script | `scenario_dev.py verifier` : la PR, la décision et son `auth_time`, puis la relance du collecteur |

Variables du script : `CHOREGOS_DEV_URL`, `CHOREGOS_DEV_TOKEN`, `CHOREGOS_DEV_ORG`, `ESSAI_DEPOT` (voir son en-tête) — aucune ne suppose un locataire.

**Valider (étape 4)** : une décision exige une session humaine. Un jeton d'API est refusé (403
`decision_requires_session`), et c'est voulu. Après `?reauth=1`, dans la console du navigateur, sur
la page de la plateforme :

```js
await fetch("/api/v1/projects/<projet>/proposals/<proposition>/decision", {
  method: "POST", headers: {"Content-Type": "application/json"},
  body: JSON.stringify({decision: "approve", reason: "diff relu"}),
}).then(r => r.json())
```

## Ce que ce parcours ne prouve pas

- **la console** : la boîte de validation (SOC-134) n'existe pas encore, la décision passe par l'API ;
- **le collecteur réel** : le rapport est posté par le script, au format observations NDJSON v1 ;
- **une correction appliquée** : la PR n'est fusionnée par personne, et la relance du collecteur
  est simulée.
