# ADR-0004 — Le coût se compte au gateway, pas chez l'agent

- **État** : acceptée
- **Concerne** : S4, S1

## Contexte

Un agent peut se tromper sur sa consommation, ou mentir. Une plateforme qui facture
d'après ce que l'agent déclare ne sait pas ce qu'elle dépense.

## Décision

Tous les appels de modèles passent par LiteLLM. Chaque run reçoit une **clé virtuelle**
dédiée, avec un **plafond dur** égal au budget de l'étape et une durée de vie courte.
Le coût est lu au gateway (`/key/info`, `/spend/logs`), jamais dans le `StageResult` — le
contrat ne prévoit même pas de champ pour ça.

Quand le plafond est atteint, LiteLLM refuse : l'agent reçoit une erreur, le runner
termine `failed(reason=budget)`, l'orchestrateur escalade vers un humain.

## Conséquences

- Le coût affiché dans le ticket est le coût réel, à la requête près.
- Un agent qui s'emballe coûte au maximum le budget de son étape.
- Un modèle local est comptabilisé avec un **prix interne** configuré, pour rester comparable.
- La clé est révoquée dès la fin du run : elle ne sert plus à rien si elle fuit.

## Alternatives écartées

- **Compter les tokens côté runner** : duplique la logique de tarification et rate le cache.
- **Faire confiance au `StageResult`** : ce serait la seule mesure non vérifiée de la chaîne.
