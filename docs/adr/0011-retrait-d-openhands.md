# 0011 — Retrait d'OpenHands

- **Statut** : accepté, 2026-09-21
- **Remplace** : le choix d'OpenHands comme backend par défaut (`docs/plan/02-orchestrateur-agents-runner.md`)

## Contexte

Le plan faisait d'OpenHands le backend par défaut, lancé en `openhands acp`. Confronté à son
vrai binaire dans l'image du runner, ce lancement n'existe pas :

- la **0.59** n'a que deux sous-commandes, `serve` et `cli` ; `openhands acp` retombe
  silencieusement sur `cli` ;
- la **1.x** n'a plus de binaire `openhands` du tout. Son point d'entrée est `agent-server`,
  un serveur HTTP, et le module `openhands` n'est plus importable.

Le plan le disait à demi-mot (« `openhands acp` **ou** `agent-server` + client ACP »). Le code
avait retenu la première branche comme un fait, et la suite de conformité passait parce
qu'elle tourne contre des fakes. Un projet provisionné avec le défaut échouait donc à son
premier ticket.

OpenHands était aussi le plus lourd de l'image : son arbre Python portait quatre des quatorze
CRITICAL relevés par Trivy (`litellm`, `fastmcp`, `anyio`, `GitPython`, dont une exécution de
code à distance), et c'est lui qui rendait la construction arm64 interminable sous émulation.

## Décision

OpenHands est retiré.

- Il n'est plus installé dans l'image `choregos-runner`, ni ses dépendances transitives.
- Il n'est plus dans le registre des backends. `get_backend("openhands")` le **refuse avec sa
  raison** (`RETIRED` dans `choregos_runner.backends`) plutôt qu'avec un « backend inconnu »
  qui ferait croire à une faute de frappe.
- Le défaut devient `claude-code` partout où il était écrit : schéma et modèle du contrat,
  templates, résolveur de modèles, évals, et l'ordre de préférence `KNOWN_BACKEND_NAMES`, qui
  plaçait OpenHands **en tête**.

Restent installés et appelés à la construction de l'image : `claude-code-acp`, `gemini`
(`--acp`) et `opencode` (`acp`).

## Conséquences

- Un projet qui aurait `default_backend: openhands` dans sa configuration échoue au premier
  run avec un message qui dit pourquoi et vers quoi migrer.
- `claude-code` n'accepte que des modèles Claude, contrainte dure. Or l'orchestrateur ne
  charge pas le catalogue du gateway : un alias `platform/*` n'y est jamais résolu, et la
  contrainte le refusait **toujours** — toute étape échouait dès `prepare_stage`. Sur un alias
  non résolu, la contrainte devient donc un avertissement ; elle reste bloquante dès que le
  vrai modèle est connu. Le gateway de la plateforme doit donc router les alias utilisés par
  un projet `claude-code` vers des modèles Claude.
- L'image du runner perd l'arbre Python d'OpenHands : plus légère, plus rapide à construire,
  et quatre CRITICAL de moins à surveiller.

## Pour y revenir

Deux conditions, dans cet ordre : qu'OpenHands expose un agent ACP (ou qu'on écrive un client
ACP vers son `agent-server`), puis que ce backend passe la suite de conformité **contre le
binaire réel** dans l'image, pas contre un fake. L'adaptateur retiré est dans l'historique
(`packages/runner/src/choregos_runner/backends/openhands.py`, avant ce commit).
