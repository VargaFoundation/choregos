# La démonstration : deux projets, un seul moteur

Un cluster **mono-nœud**, la plateforme et toutes ses dépendances déployées par le chart,
et deux projets qui n'ont rien en commun :

| Projet | Ce que fait l'agent | Ce que ça prouve |
|---|---|---|
| `panier` | Lit un ticket, écrit du code et son test dans un dépôt git, pousse une branche | La chaîne complète : ticket → agent → diff → garanties → état suivant |
| `staffing` | Lit un besoin RH, propose des profils, les qualifie | Le moteur ne parle pas de logiciel : mêmes états, mêmes budgets, mêmes garanties, playbooks et sorties du métier. **Ce projet n'a aucun dépôt** — l'agent travaille dans un répertoire vide |

Tout ce qui tourne ici est le vrai chemin de la plateforme. **Une seule chose est simulée** :
le tracker (pas de GitHub ni de Jira sur un banc), donc les tickets sont posés en base par
`seed.py`. Les agents, eux, travaillent pour de bon.

## Monter le banc

Compter **une vingtaine de minutes** la première fois (construction des images), un
nœud avec 8 Go et 4 CPU libres, et Docker, kind, kubectl, helm.

```bash
make demo-up        # cluster kind mono-nœud, namespace, dépôt git, ConfigMaps, chart
make demo-images    # construit local/choregos-*:demo et les CHARGE dans le nœud kind
```

Le chart de la démonstration attend des images locales (`imagePullPolicy: Never`) : sans
`make demo-images`, chaque pod reste en `ErrImageNeverPull`. Le chart embarque PostgreSQL,
Temporal, la mémoire (Ecphoria) et la passerelle LLM (LiteLLM) — c'est le sens de
`global.<dépendance>.embedded`.

## Donner un accès modèle aux agents

La démonstration passe par **la passerelle embarquée** : la plateforme frappe une clé par
run, plafonnée, et compte la dépense au registre — c'est le seul mode où le coût par ticket
est un chiffre mesuré et pas une case vide. La passerelle a besoin d'une clé de
fournisseur, qu'elle seule voit :

```bash
kubectl -n choregos create secret generic platform-llm-key --from-literal=api-key=sk-ant-…
kubectl -n choregos rollout restart deploy/choregos-litellm
```

Sans clé de fournisseur (un abonnement Claude Code seulement), le banc tourne en mode
**direct** : l'agent apporte ses identifiants, et **rien n'est compté ni plafonné** hors
tours et minutes. À dire quand on montre le tableau de bord.

```bash
kubectl -n choregos create secret generic agent-creds \
  --from-literal=CLAUDE_CODE_OAUTH_TOKEN="$(jq -r .claudeAiOauth.accessToken ~/.claude/.credentials.json)"
helm upgrade choregos charts/choregos -n choregos -f charts/choregos/values/local.yaml \
  -f demo/values-demo.yaml --set global.gateway.embedded=false --reuse-values
make demo-seed GATEWAY=direct
```

Dans les deux cas le secret est monté **par référence** dans les pods, jamais recopié dans
une spécification.

## Poser les projets et les tickets

```bash
make demo-seed
```

Le Job pose l'organisation, les deux projets, leurs workflows et leurs tickets, puis
démarre chaque ticket — l'équivalent de `mark_agent_ready` dans l'API : un workflow
Temporal par ticket, qui lit l'état, choisit la transition et lance l'agent.

## Savoir que ça a marché

```bash
make demo-status
```

Ce qu'on doit voir, et dans quel délai :

| Quoi | Attendu | Quand |
|---|---|---|
| `DEMO-1..3` | `done` | ~10 min |
| `RH-1`, `RH-2` | `a_valider` (en attente du manager) | ~5 min |
| registre, `kind=model` | des jetons et des euros **non nuls** | dès le premier run |
| registre, `kind=tool` | au moins une ligne (`verifier_adresse`, gratuit) | au premier sourcing |

Un ticket qui reste dans son état de départ plus de 15 minutes n'attend pas : il est mort.
`kubectl -n choregos exec deploy/choregos-temporal -- temporal workflow describe -w
wi-staffing-RH-1` dit pourquoi. (Ce que l'interface devrait dire elle-même — chantier P0-2
de l'état des lieux du 2026-09-24.)

## Ce que ce banc ne prouve pas

Le connecteur SCM est un `fake` : il ne sait pas comparer deux branches. Les garanties qui
lisent le diff — périmètre respecté, taille du diff, absence de secrets — **refusent donc
de se prononcer** plutôt que de passer à vide, et le workflow de démonstration ne les
déclare pas. Sur un vrai dépôt (GitHub), elles se remettent : c'est une limite du banc,
pas du moteur. Le reste est réel, y compris la vérification des preuves d'exécution.

## Rejouer la démonstration

Un ticket n'a **qu'un** interpréteur : Temporal refuse de redémarrer un workflow déjà
terminé sous le même identifiant (`ALLOW_DUPLICATE_FAILED_ONLY`). C'est voulu — c'est ce
qui empêche de traiter deux fois le même ticket. Rejouer demande donc de **nouveaux
tickets** : `make demo-seed SERIE=b` pose `DEMO-1b`, `RH-1b`… à côté des précédents.

Remettre le dépôt de démonstration à zéro entre deux essais :

```bash
kubectl -n choregos exec deploy/demo-git -- sh -c \
  'cd /srv/git/app.git && for b in $(git for-each-ref --format="%(refname:short)" refs/heads | grep choregos/); do git branch -D $b; done'
```

## Regarder

```bash
kubectl -n choregos logs -l choregos/run-id --tail=50 -f         # les agents au travail
kubectl -n choregos port-forward deploy/choregos-web 3000:3000   # le front
```

Le dépôt de la démonstration se relit depuis le cluster :

```bash
kubectl -n choregos exec deploy/demo-git -- git -C /srv/git/app.git log --oneline --all
```
