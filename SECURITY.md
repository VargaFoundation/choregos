# Politique de sécurité

## Signaler une vulnérabilité

Écrivez à **security@varga.foundation** (ou ouvrez un *security advisory* privé sur GitHub).
N'ouvrez pas d'issue publique : nous préférons corriger avant de publier.

Merci d'inclure : la version ou le commit, ce que vous avez observé, comment le reproduire,
et l'impact que vous estimez. Nous accusons réception sous 3 jours ouvrés et donnons un
premier avis sous 10 jours.

## Versions suivies

| Version | Corrections de sécurité |
| :-- | :-- |
| `1.x` (dernière mineure) | oui |
| versions antérieures | non |

## Ce que nous considérons comme une vulnérabilité

- Toute façon pour un agent d'écrire hors de son périmètre sans être détecté.
- Toute fuite de secret : dans un workspace, une image, un journal, un transcript.
- Tout contournement d'un des trois verrous de production (merge queue, train, garde-fous).
- Toute élévation de privilège dans le cluster depuis un runner.
- Tout accès aux données d'une autre organisation (RLS, RBAC).

## Ce que nous ne considérons pas comme une vulnérabilité

- Un agent qui produit du mauvais code : c'est le rôle de la CI et de la review.
- Un modèle qui suit une instruction injectée **sans que cela lui donne un pouvoir** : les
  mécanismes (périmètre, absence de credential, gates) s'appliquent de la même façon.
- Le mode `CHOREGOS_FAKES=1`, qui n'est pas destiné à la production.

## Nos engagements

- Les images sont signées (cosign keyless) et accompagnées d'un SBOM.
- Les dépendances sont scannées chaque nuit ; un CRITICAL bloque la publication.
- Les secrets tournent selon le calendrier de `docs/runbooks/rotation-secrets.md`.
