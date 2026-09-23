# La démonstration : deux projets, un seul moteur

Un cluster **mono-nœud**, la plateforme et toutes ses dépendances déployées par le chart,
et deux projets qui n'ont rien en commun :

| Projet | Ce que fait l'agent | Ce que ça prouve |
|---|---|---|
| `panier` | Lit un ticket, écrit du code et son test dans un dépôt git, pousse une branche | La chaîne complète : ticket → agent → diff → garanties → état suivant |
| `staffing` | Lit un besoin RH, propose des profils, les qualifie | Le moteur ne parle pas de logiciel : mêmes états, mêmes budgets, mêmes garanties, playbooks et sorties du métier |

Tout ce qui tourne ici est le vrai chemin de la plateforme. **Une seule chose est simulée** :
le tracker (pas de GitHub ni de Jira sur un banc), donc les tickets sont posés en base par
`seed.py`. Les agents, eux, travaillent pour de bon.

## Monter le banc

```bash
kind create cluster --config demo/kind.yaml            # un seul nœud
kubectl create ns choregos
kubectl -n choregos apply -f demo/manifests/git.yaml   # le dépôt git du cluster

helm upgrade --install choregos charts/choregos -n choregos \
  -f charts/choregos/values/local.yaml -f demo/values-demo.yaml
```

Le chart embarque PostgreSQL, Temporal, la mémoire (Ecphoria) et, si on veut, la passerelle
LLM. Rien d'autre à fournir — c'est le sens de `global.<dépendance>.embedded`.

## Donner un accès modèle aux agents

La démonstration ne monte pas de passerelle LLM : les agents apportent leurs identifiants,
et le projet emploie le connecteur `gateway: direct`. **Conséquence assumée : aucun coût
n'est mesuré et aucun plafond de dépense ne s'applique** — seuls les budgets en tours et en
minutes tiennent encore.

Avec un abonnement Claude Code :

```bash
kubectl -n choregos create secret generic agent-creds \
  --from-literal=CLAUDE_CODE_OAUTH_TOKEN="$(jq -r .claudeAiOauth.accessToken ~/.claude/.credentials.json)"
```

Avec une clé d'API :

```bash
kubectl -n choregos create secret generic agent-creds \
  --from-literal=ANTHROPIC_API_KEY=sk-ant-…
```

Le secret est monté **par référence** dans chaque pod d'agent (`envFrom`), jamais recopié
dans une spécification.

## Poser les projets et les tickets

```bash
kubectl -n choregos create configmap demo-seed \
  --from-file=seed.py=demo/seed.py \
  --from-file=demo-code.yaml=demo/workflows/demo-code.yaml \
  --from-file=staffing.yaml=demo/workflows/staffing.yaml
kubectl -n choregos create configmap demo-playbooks --from-file=demo/playbooks
kubectl -n choregos apply -f demo/manifests/seed-job.yaml
```

Le Job démarre aussi chaque ticket (`--demarrer`), c'est-à-dire l'équivalent de
`mark_agent_ready` dans l'API : un workflow Temporal par ticket, qui lit l'état, choisit la
transition et lance l'agent.

## Ce que ce banc ne prouve pas

Le connecteur SCM est un `fake` : il ne sait pas comparer deux branches. Les garanties qui
lisent le diff — périmètre respecté, taille du diff, absence de secrets — **refusent donc
de se prononcer** plutôt que de passer à vide, et le workflow de démonstration ne les
déclare pas. Sur un vrai dépôt (GitHub), elles se remettent : c'est une limite du banc,
pas du moteur. Le reste est réel, y compris la vérification des preuves d'exécution.

## Rejouer la démonstration

Un ticket n'a **qu'un** interpréteur : Temporal refuse de redémarrer un workflow déjà
terminé sous le même identifiant (`ALLOW_DUPLICATE_FAILED_ONLY`). C'est voulu — c'est ce
qui empêche de traiter deux fois le même ticket. Pour rejouer la démonstration, il faut
donc de **nouveaux tickets** : changer les clés dans `seed.py` (`DEMO-4`, `DEMO-5`…), ou
repartir d'un cluster neuf.

Remettre le dépôt de démonstration à zéro entre deux essais :

```bash
kubectl -n choregos exec deploy/demo-git -- sh -c \
  'cd /srv/git/app.git && for b in $(git for-each-ref --format="%(refname:short)" refs/heads | grep choregos/); do git branch -D $b; done'
```

## Regarder

```bash
kubectl -n choregos get pods -l choregos/run-id            # les agents au travail
kubectl -n choregos logs -l choregos/run-id --tail=50 -f
kubectl -n choregos port-forward deploy/choregos-web 3000:3000   # le front
```

Le dépôt de la démonstration se relit depuis le cluster :

```bash
kubectl -n choregos exec deploy/demo-git -- git -C /srv/git/app.git log --oneline --all
```
