# Vérifier l'exécuteur ACA contre un vrai abonnement Azure

## À quoi ça sert

`packages/adapters/tests/test_aca.py` prouve la **forme** des appels ARM contre un transport
simulé : les 18 tests passent sans qu'aucun abonnement n'ait jamais accepté quoi que ce soit. Ce
runbook monte un environnement jetable et fait tourner `tests/live/test_aca_live.py` contre lui —
ce qui prouve la seule chose que le transport simulé ne dira jamais : qu'Azure accepte nos appels.

Comptez **quinze minutes**, dont dix d'attente pendant la création de l'environnement managé.

## Ce que ça coûte

Un environnement Container Apps *Consumption* ne facture que ce qui tourne : les jobs du test
durent quelques dizaines de secondes à 0,5 vCPU. L'espace Log Analytics a un quota gratuit
largement suffisant. **Le groupe de ressources est jetable** : tout est supprimé d'une commande à
la fin, et c'est la dernière étape, pas une option.

## Monter l'environnement

```bash
az login                                   # si besoin
az account set --subscription <id>
az provider register -n Microsoft.App      # une fois par abonnement (~2 min)
az extension add -n containerapp --yes

RG=choregos-aca-test
az group create -n "$RG" -l westeurope \
  --tags purpose=choregos-s13-05 delete-after=today

# Crée aussi l'espace Log Analytics associé. C'est l'étape longue (5 à 10 minutes).
az containerapp env create -n choregos-env -g "$RG" -l westeurope \
  --logs-destination log-analytics
```

## Lancer la suite

```bash
export CHOREGOS_LIVE_AZURE_SUBSCRIPTION=$(az account show --query id -o tsv)
export CHOREGOS_LIVE_AZURE_RG="$RG"
export CHOREGOS_LIVE_AZURE_ENV_ID=$(az containerapp env show -n choregos-env -g "$RG" --query id -o tsv)
# Jeton utilisateur plutôt que service principal : rien à créer, donc rien à supprimer.
# Il expire en une heure — le relancer si la suite traîne.
export CHOREGOS_LIVE_AZURE_TOKEN=$(az account get-access-token \
  --resource https://management.azure.com --query accessToken -o tsv)

uv run pytest tests/live/test_aca_live.py -m live -v
```

Les six tests couvrent : le cycle complet (créé → déclenché → état terminal), `start` rejoué qui ne
double pas l'exécution, le jeton de run absent de ce que rend ARM, l'annulation, un job inexistant
traité comme une absence, et le message des logs sans workspace configuré.

## Supprimer — obligatoire

```bash
az group delete -n "$RG" --yes --no-wait
az group exists -n "$RG"        # false quand c'est fini (une à deux minutes)
```

La suppression du groupe emporte l'environnement, l'espace Log Analytics et tout job qu'un test
aurait laissé derrière lui. Chaque test supprime déjà son job dans un `finally`, mais un `pytest`
interrompu au mauvais moment peut en laisser un : le groupe est la garantie.

## Si ça coince

| Symptôme | Cause | Quoi faire |
|:--|:--|:--|
| `The subscription is not registered to use namespace 'Microsoft.App'` | Provider pas enregistré | `az provider register -n Microsoft.App`, puis attendre `Registered` |
| L'environnement reste en `Waiting` | Normal les 5 à 10 premières minutes | Attendre ; au-delà de 20 min, `az containerapp env show` et lire `properties` |
| `401` sur ARM en cours de suite | Le jeton a expiré (1 h) | Réexporter `CHOREGOS_LIVE_AZURE_TOKEN` |
| Un test laisse un job derrière | `pytest` interrompu | `az containerapp job delete -n <nom> -g "$RG" --yes`, ou supprimer le groupe |

## Ce que ça ne prouve pas

- **Pas d'identité managée ni de registre privé.** Les tests tirent une image publique ; le chemin
  `identity_id` + `registry_server` reste vérifié contre le protocole seulement. Pour l'éprouver il
  faut une identité assignée et un ACR, c'est-à-dire des ressources à créer et à supprimer.
- **Pas de logs depuis Log Analytics.** L'ingestion prend plusieurs minutes après la fin d'un
  conteneur, bien au-delà de ce qu'un test doit attendre. La requête KQL est écrite et le test
  vérifie le message de repli ; la lire pour de vrai se fait à la main, après coup.
- **Pas `azure-devops-aca`.** Le template complet demande en plus Azure Boards (tracker) et un CD
  sur révisions ACA — un abonnement Azure ne suffit pas, il faut une organisation Azure DevOps.
