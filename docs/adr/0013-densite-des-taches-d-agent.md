# 0013 — La densité des tâches d'agent, et ce qu'on prend à AX

- **Statut** : accepté, 2026-09-24
- **Concerne** : l'exécution des étapes d'agent (`Executor`), le chart, l'exploitation

## Contexte

Google a publié [AX](https://github.com/google/ax) (Apache 2.0), un orchestrateur déclaratif
de tâches d'agent : bac à sable, workspace, passerelle réseau, modèle, suspend/resume, sur un
plan de contrôle Kubernetes. Il annonce viser « des milliards de charges d'agent dans un
cluster », au-dessus d'[Agent Substrate](https://cloud.google.com/blog/products/containers-kubernetes/bringing-you-agent-sandbox-on-gke-and-agent-substrate)
et de gVisor.

La question posée n'est pas « faut-il l'adopter » mais **ce qu'il fait que nous ne faisons
pas**. Et un problème réel se cachait derrière : Choregos lance **un Job par étape d'agent**,
sans aucun plafond. Dix tickets qui démarrent ensemble font dix pods qui tirent la même image
en même temps, sur un cluster qui a autre chose à faire.

## Ce qu'AX fait, et où nous en sommes

| Capacité d'AX | Chez nous |
|---|---|
| Bac à sable par tâche, limites CPU/mémoire | `Executor` × 4 (Tekton, Job Kubernetes, ACA, Docker), gVisor par `RuntimeClass` |
| Passerelle réseau en liste blanche | NetworkPolicies et CiliumNetworkPolicy, **egress vérifié sur cluster** (7 tests) |
| Modèle et identifiants | Profils de modèles, clé virtuelle **par run** avec plafond dur, coût au ledger |
| Workspace (git + MCP pré-câblés) | `StageInput` : dépôt, branche, serveurs MCP, playbook — par run |
| Phases et conditions | États de run, événements, SSE reprenable |
| **Plafond et file d'attente** | **Rien. C'est le manque, et il est corrigé ici.** |
| **Suspend/resume d'un agent EN COURS** | Rien, et ce n'est pas à notre portée (voir plus bas) |
| `ax ssh` dans le bac à sable | Refusé délibérément (voir plus bas) |

## Décision 1 — La file d'attente est faite par Kubernetes lui-même

`runner.maxActive` borne le nombre de runs **simultanés** par namespace. Au-delà, le Job est
créé avec `spec.suspend: true` : il n'a aucun pod, ne tire aucune image, ne prend aucune place
dans le quota. L'admission se fait au fil des relevés d'état — l'orchestrateur interroge déjà
chaque run, donc chaque tour de boucle est une occasion d'admettre le suivant — et **dans
l'ordre d'arrivée**, faute de quoi le dernier ticket posé passerait devant à chaque fois.

Ce n'est pas un ordonnanceur, et le plafond est **souple** : deux runs peuvent s'admettre dans
la même fenêtre et dépasser d'un. C'est le prix de n'ajouter aucun composant, et il est sans
commune mesure avec le problème évité. `0` garde le comportement d'avant : un déploiement qui
ne demande rien ne change pas de régime du jour au lendemain.

Le banc mono-nœud passe à `maxActive: 2`, parce que c'est exactement là que la pointe fait mal.

## Décision 2 — Pas de pool de runners tièdes, et la raison est la sécurité

La densité maximale s'obtiendrait avec un pool de pods runner tièdes qui enchaînent les
étapes : plus de création de pod, plus de tirage d'image, caches chauds. Nous **refusons**, et
il faut dire pourquoi, parce que la tentation reviendra.

Aujourd'hui, le jeton d'un run est un Secret monté **par référence** dans le pod de ce run, et
il meurt avec lui. Un pod qui enchaîne deux runs porte forcément, à un instant, de quoi
obtenir les identifiants du second — donc un droit plus large qu'un seul run. On remplacerait
une frontière que Kubernetes tient (un pod, un run, un jeton) par une frontière que notre code
prétendrait tenir. La frontière d'isolation reste le **run**, pas le projet.

Ce qui rendrait le pool acceptable : un bac à sable capable de repartir d'un instantané propre
entre deux runs. C'est précisément ce qu'Agent Substrate apporte — et c'est la vraie raison de
regarder AX plus tard, pas ses primitives.

## Décision 3 — Ce qu'on ne prend pas, et pourquoi

- **Agent Substrate comme dépendance** : Google le marque lui-même « not an officially
  supported Google product ». AX annonce par ailleurs des ruptures majeures avant sa
  stabilisation. Nous échangerions du code éprouvé contre une dépendance non supportée.
- **Le plan de contrôle d'AX** (`ax-system`, ressources cluster-scoped) : un locataire de
  notre plateforme cible n'a pas le droit d'en poser — l'AppProject le lui interdit. AX serait
  inutilisable là où nous déployons.
- **`ax ssh` dans le bac à sable** : notre Role de locataire exclut délibérément `pods/exec`.
  Un humain qui entre dans le pod d'un agent voit ses secrets et peut agir en son nom, hors de
  toute trace. Le journal du run, son transcript et ses preuves sont la réponse — et ils sont
  archivés, ce qu'une session interactive n'est pas.
- **Le suspend/resume d'une étape EN COURS** : Kubernetes ne sait pas suspendre un Job déjà
  démarré sans détruire son pod. Le faire proprement demande un instantané du bac à sable.
  Reste ouvert, honnêtement : aujourd'hui, un nœud perdu au milieu d'une étape nous la fait
  **rejouer entière**, et nous la repayons.

## Décision 4 — La couture est posée maintenant, pas le jour où on en aura besoin

Un exécuteur **annonce ce qu'il sait faire** (`capabilities`), pris dans un vocabulaire fermé :
`queue`, `suspend`, `resume`, `snapshot`. Aujourd'hui, un seul exécuteur remplit une seule
capacité — `k8s_job` sait mettre en file. C'est précisément pour cela qu'il faut le poser
maintenant : le jour où un bac à sable à instantané arrive, l'orchestrateur **demandera** ce
que le runtime sait faire au lieu de le supposer, et rien d'autre ne bougera.

Une suite de conformité refuse qu'un exécuteur annonce une capacité qu'il n'implémente pas :
une capacité annoncée et absente est pire qu'absente, parce que l'appelant s'y fie. Elle porte
aussi un test qui dit un **état** plutôt qu'une règle — « aucun exécuteur ne sait encore
prendre un instantané ». Le jour où il échoue, c'est le signal de relire cet ADR : le pool de
runners tièdes redevient défendable, et la perte d'un nœud cesse de tout faire rejouer.

## Conséquences

- `runner.maxActive` dans le chart, `CHOREGOS_RUNNER_MAX_ACTIVE` pour l'exécuteur `k8s_job`.
- Un run en attente le dit : `en attente d'une place (plafond N par namespace)`.
- Trois tests sans cluster : le plafond retient, la file avance dans l'ordre, et sans plafond
  rien ne change.
- Un défaut trouvé en chemin : notre client Kubernetes écrasait les en-têtes de l'appelant, ce
  qui rendait tout `PATCH` impossible — il doit annoncer son `Content-Type: application/merge-patch+json`.
