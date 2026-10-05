# Essai S15-07 — un vrai Claude Code contre la porte MCP du locataire dev

Joué le **2026-10-05** contre `http://choregos.internal.dev.diametral.com` (Choregos **0.14.0**,
`choregos-deploy` #21), depuis un poste sur le VPN, avec Claude Code **2.1.289**.

```bash
CHOREGOS_DEV_TOKEN="$(cat ~/.config/choregos/dev-token)" essai/integration-claude/run.sh
```

Le jeton `*` d'un humain ne sert qu'à préparer et à vérifier en REST ; il n'entre jamais dans la
configuration de Claude Code. Le script frappe pour l'essai un jeton `mcp:write`, lié au projet
`diametral:essai-it4it`, valable un jour, écrit dans un fichier 0600 d'un répertoire temporaire,
et le **révoque** en sortant (vérifié après coup : il n'est plus listé).

## Ce qui a été observé

| Étape | Attendu | Observé |
|---|---|---|
| 1 | `POST /mcp` sans jeton : 401 | 401, `WWW-Authenticate: Bearer realm="choregos"` |
| 2 | Un jeton `mcp:write` lié au projet | créé (`01a10ca9-…`) |
| 3 | `claude -p … --mcp-config … --strict-mcp-config` : le serveur `choregos` est `connected` et annonce `create_work_item` | `connected`, **21 outils** : les 7 de la porte et les 14 de l'ontologie IT4IT du projet (`finding_search`, `host_get`, `action_open_infra_pr`…) |
| 4 | Claude Code ouvre un ticket par `create_work_item` | le modèle répond `ESSAI-IT4IT-2` |
| 5 | Vérification **en REST**, sans croire le modèle | le ticket `ESSAI-IT4IT-2` existe (`inbox`) ; le jeton a servi (`last_used_at` 15:23:27Z) et `last_client` vaut `claude-code/2.1.289 (sdk-cli)` |

Les outils de l'ontologie passent la porte du projet avec les droits de la personne (S15-03) ;
aucun outil ne décide (`action_*` propose, la décision se prend dans la console).

## Ce que l'essai ne prouve pas

- **OAuth** : claude.ai et les clients hébergés appellent depuis le réseau de leur éditeur, et le
  dev n'a pas d'adresse publique (S15-08 vérifie la porte contre un faux IdP).
- **L'écriture d'une proposition par MCP** sur le dev : l'essai n'appelle que `create_work_item`.
- Un autre client que Claude Code (Cursor, VS Code) : les extraits de la page Integrations sont
  vérifiés par les tests de la console, pas joués ici.

## Ce qui reste sur le dev

Le ticket `ESSAI-IT4IT-2` (titre « Essai MCP 20261005T…Z », non démarré), trace de l'essai.
