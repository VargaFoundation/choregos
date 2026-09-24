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

- Les images sont signées (cosign keyless) et vérifiées par digest à l'admission (Kyverno).
- Un CRITICAL Trivy bloque la publication des images de `main`.
- Les secrets tournent selon le calendrier de `docs/runbooks/rotation-secrets.md`.

## Ce que nous ne garantissons PAS encore (et que nous disions garantir)

Écrit le 2026-09-24, après un état des lieux qui a trouvé ce fichier plus optimiste que le
dépôt :

- **Pas de SBOM** avec les images, et **pas de scan des images de release** : prévu (P1-2 de
  `docs/plan/STATE-OF-THE-PROJECT-2026-09-24.md`). Le scan nocturne des dépendances ne bloque
  rien pour l'instant.
- **L'isolation des organisations** (RLS PostgreSQL) est fail-closed et testée sur un vrai
  PostgreSQL depuis le 2026-09-24 — elle ne l'était pas avant. Elle suppose que l'API ne se
  connecte **pas en superutilisateur** (l'API refuse de démarrer ainsi en staging/prod).
- **Les garde-fous du runner** (périmètre de chemins, commandes) sont *fail-open* par
  construction : ce qu'ils ne savent pas classer est autorisé et journalisé. Le sandbox
  (NetworkPolicy, non-root, pas de jeton de compte de service) et la vérification du diff
  après coup sont les mécanismes durs ; les garde-fous sont un filet, pas un mur.
- **La connexion de développement** (`/auth/login?as=`) est éteinte par défaut et refusée
  en staging/prod. Une installation qui l'allume est ouverte à quiconque atteint l'API.
