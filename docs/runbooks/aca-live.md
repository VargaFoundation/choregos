# Checking the ACA executor against a real Azure subscription

## What it is for

`packages/adapters/tests/test_aca.py` proves the **shape** of the ARM calls against a mock
transport: the 18 tests pass without any subscription ever having accepted anything. This
runbook sets up a throwaway environment and runs `tests/live/test_aca_live.py` against it —
which proves the one thing the mock transport will never say: that Azure accepts our calls.

Count **fifteen minutes**, ten of them waiting for the managed environment to be created.

## What it costs

A *Consumption* Container Apps environment only bills what runs: the test's jobs last a few
tens of seconds at 0.5 vCPU. The Log Analytics workspace has a free quota that is more than
enough. **The resource group is throwaway**: everything is deleted with one command at the
end, and that is the last step, not an option.

## Set up the environment

```bash
az login                                   # if needed
az account set --subscription <id>
az provider register -n Microsoft.App      # once per subscription (~2 min)
az extension add -n containerapp --yes

RG=choregos-aca-test
az group create -n "$RG" -l westeurope \
  --tags purpose=choregos-s13-05 delete-after=today

# Also creates the associated Log Analytics workspace. This is the long step (5 to 10 minutes).
az containerapp env create -n choregos-env -g "$RG" -l westeurope \
  --logs-destination log-analytics
```

## Run the suite

```bash
export CHOREGOS_LIVE_AZURE_SUBSCRIPTION=$(az account show --query id -o tsv)
export CHOREGOS_LIVE_AZURE_RG="$RG"
export CHOREGOS_LIVE_AZURE_ENV_ID=$(az containerapp env show -n choregos-env -g "$RG" --query id -o tsv)
# A user token rather than a service principal: nothing to create, so nothing to delete.
# It expires in one hour — re-export it if the suite drags on.
export CHOREGOS_LIVE_AZURE_TOKEN=$(az account get-access-token \
  --resource https://management.azure.com --query accessToken -o tsv)

uv run pytest tests/live/test_aca_live.py -m live -v
```

The six tests cover: the full cycle (created → triggered → terminal state), a replayed
`start` that does not double the execution, the run token absent from what ARM returns,
cancellation, a non-existent job treated as an absence, and the logs message without a
configured workspace.

## Delete — mandatory

```bash
az group delete -n "$RG" --yes --no-wait
az group exists -n "$RG"        # false when done (one to two minutes)
```

Deleting the group takes the environment, the Log Analytics workspace and any job a test
may have left behind. Every test already deletes its job in a `finally`, but a `pytest`
interrupted at the wrong moment can leave one: the group is the guarantee.

## If it gets stuck

| Symptom | Cause | What to do |
|:--|:--|:--|
| `The subscription is not registered to use namespace 'Microsoft.App'` | Provider not registered | `az provider register -n Microsoft.App`, then wait for `Registered` |
| The environment stays `Waiting` | Normal for the first 5 to 10 minutes | Wait; beyond 20 min, `az containerapp env show` and read `properties` |
| `401` on ARM mid-suite | The token expired (1 h) | Re-export `CHOREGOS_LIVE_AZURE_TOKEN` |
| A test leaves a job behind | `pytest` interrupted | `az containerapp job delete -n <name> -g "$RG" --yes`, or delete the group |

## What it does not prove

- **No managed identity nor private registry.** The tests pull a public image; the
  `identity_id` + `registry_server` path stays verified against the protocol only. Proving
  it needs an assigned identity and an ACR, i.e. resources to create and delete.
- **No logs from Log Analytics.** Ingestion takes several minutes after a container ends,
  far beyond what a test should wait. The KQL query is written and the test checks the
  fallback message; reading it for real is done by hand, afterwards.
- **Not `azure-devops-aca`.** The full template also needs Azure Boards (tracker) and a CD
  on ACA revisions — an Azure subscription is not enough, an Azure DevOps organisation is
  required.
