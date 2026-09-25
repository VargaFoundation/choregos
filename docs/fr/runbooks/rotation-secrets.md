# Rotation des secrets

## Calendrier

| Secret | Période | Effet d'une rotation ratée |
| :-- | :-- | :-- |
| clé privée de l'App GitHub `choregos-bot` | 90 j | plus de webhooks, plus de PR, plus de tokens de run |
| `master_key` LiteLLM | 90 j | plus aucun appel de modèle : tous les runs échouent |
| clés JWT ES256 de l'API (jetons de run) | 30 j | les runs en cours ne peuvent plus poster leur résultat |
| secrets de webhook (GitHub, générique) | 180 j | les webhooks sont rejetés en 401 |

## Principe : deux clés valides pendant la transition

Les jetons de run vivent jusqu'à 2 h. Une rotation « d'un coup » casse les runs en cours.
L'API accepte donc **deux** clés de vérification pendant la fenêtre de rotation.

## App GitHub

1. Générer une nouvelle clé privée dans les réglages de l'App.
2. L'écrire dans le coffre : `choregos/api` → `github-app-private-key-next`.
3. Déployer : l'API essaie la nouvelle, puis l'ancienne.
4. Après 24 h sans erreur, supprimer l'ancienne clé côté GitHub **puis** dans le coffre.

```bash
kubectl -n choregos-system logs deploy/choregos-api | grep -c "github.*401"   # doit rester 0
```

## `master_key` LiteLLM

1. Créer la nouvelle clé dans LiteLLM (`/key/generate` avec le rôle admin).
2. Mettre à jour `choregos/gateway` → `master-key` dans le coffre.
3. Redéployer l'API et les workers (le `checksum/config` du chart force le redémarrage).
4. **Ne pas** révoquer l'ancienne avant que les runs en cours soient terminés : leurs clés
   virtuelles ont été mintées avec elle.

## Clés JWT de l'API

```bash
# Générer une paire ES256
openssl ecparam -genkey -name prime256v1 -noout -out run-token.pem
openssl ec -in run-token.pem -pubout -out run-token.pub
```

1. Écrire la nouvelle paire dans `choregos/api` (`run-token-private-key-next`).
2. Déployer : les nouveaux jetons sont signés avec la nouvelle clé, les anciens restent
   vérifiables.
3. Après `max_minutes + 15` (2 h suffisent), promouvoir la nouvelle paire et supprimer l'ancienne.

## Vérifier

- Un run de bout en bout passe (`make demo` ne suffit pas : lancer un vrai ticket S).
- Aucun 401 dans les logs de l'API sur les routes `/internal` et `/webhooks`.
