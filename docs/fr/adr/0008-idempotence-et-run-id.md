# ADR-0008 — Identifiants déterministes et idempotence partout

- **État** : acceptée
- **Concerne** : S1, S2, S6

## Contexte

Temporal rejoue les activités, les webhooks arrivent deux fois, un runner peut mourir après
avoir travaillé mais avant d'avoir répondu. Sans discipline, on crée deux workflows pour un
ticket, deux runs pour une étape, et on paie deux fois.

## Décision

Tout ce qui peut être rejoué porte un identifiant **déterministe** :

| Objet | Identifiant | Conséquence |
| :-- | :-- | :-- |
| workflow d'un ticket | `wi-<projet>-<clé>` | un ticket = un workflow, quoi qu'il arrive |
| train | `train-<projet>-<env>` | un seul train par environnement |
| run d'une étape | `<work_item>-<transition>-<tentative>` | rejouer prépare le même run |
| livraison de webhook | `(source, delivery_id)` | un rejeu ne déclenche rien |
| journal ACP | `(run_id, seq)` | un lot renvoyé n'écrit pas deux fois |
| dépense | drapeau `spend_collected` | le coût est lu une fois au gateway |

Les activités qui écrivent vérifient d'abord ce qui existe déjà, et rendent le résultat
existant plutôt que d'en créer un nouveau.

## Conséquences

- Un test de non-régression accompagne chaque chemin : « rejouer ne double ni run ni coût ».
- L'idempotence prime sur la concision : une activité fait un `SELECT` avant son `INSERT`.

## Alternatives écartées

- **Déduplication a posteriori** : trouve les doublons après les avoir payés.
