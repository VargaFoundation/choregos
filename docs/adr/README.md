# Décisions d'architecture

Une décision structurante s'écrit ici avant d'être codée, et se relit quand on se demande
« pourquoi c'est comme ça ». Format : contexte, décision, conséquences, alternatives
écartées. Une décision annulée n'est pas effacée : elle est marquée *remplacée par*.

| # | Décision | État |
| --: | :-- | :-- |
| [0001](0001-contrats-geles.md) | Les contrats sont gelés à M0 et versionnés | acceptée |
| [0002](0002-acp-comme-contrat-agent.md) | ACP comme contrat d'agent, OpenHands par défaut | acceptée |
| [0003](0003-temporal-comme-orchestrateur.md) | Temporal pour l'orchestration durable | acceptée |
| [0004](0004-cout-compte-au-gateway.md) | Le coût se compte au gateway, pas chez l'agent | acceptée |
| [0005](0005-adaptateurs-et-fakes.md) | Tout connecteur a une interface et un fake | acceptée |
| [0006](0006-trois-verrous-de-production.md) | Trois verrous indépendants protègent la production | acceptée |
| [0007](0007-memoire-optionnelle.md) | La mémoire doit faire ses preuves avant qu'on en dépende | acceptée |
| [0008](0008-idempotence-et-run-id.md) | Identifiants déterministes et idempotence partout | acceptée |
| [0009](0009-gitops-seule-source-de-verite.md) | Le cluster ne se modifie que par Git | acceptée |
| [0010](0010-les-gates-sont-des-mecanismes.md) | Une garantie est un mécanisme, jamais un prompt | acceptée |
| [0011](0011-retrait-d-openhands.md) | Retrait d'OpenHands : aucun agent ACP en ligne de commande | acceptée |
| [0012](0012-le-moteur-n-est-pas-lie-au-logiciel.md) | Le moteur n'est pas lié au logiciel (playbooks, garantie générique, tracker interne) | acceptée |
