# ADR-0002 — ACP comme contrat d'agent, OpenHands par défaut

- **État** : acceptée
- **Concerne** : S2, S13

## Contexte

L'écosystème des agents de code bouge vite et de façon inégale. Se lier à un agent, c'est
se lier à sa feuille de route ; en supporter plusieurs sans contrat commun, c'est écrire
un adaptateur par agent et par version.

## Décision

Le runner parle **ACP** (Agent Client Protocol, JSON-RPC sur stdio) et rien d'autre. Un
backend se résume à : une ligne de commande, des variables d'environnement pour le modèle,
des fichiers de configuration. OpenHands est l'agent par défaut ; Claude Code, Codex,
Gemini CLI, Goose, OpenCode et Copilot CLI sont des backends optionnels.

Une **suite de conformité** de sept vérifications garde la porte (§2.2) : démarrage,
serveurs MCP, prompt trivial, permission refusée non contournée, `result.json` valide,
respect de `max_turns`, coût visible au gateway. Un backend qui échoue est **désactivé
automatiquement** dans `platform/backends` jusqu'à correction.

## Conséquences

- Changer d'agent est une ligne de configuration, pas un chantier.
- Un agent qui régresse est écarté sans discussion et sans incident de production.
- On dépend d'un protocole jeune : la suite de conformité est notre filet.

## Alternatives écartées

- **Un seul agent** : dépendance totale à sa feuille de route et à ses limites.
- **Adaptateur maison par agent** : coût de maintenance proportionnel au nombre d'agents.
