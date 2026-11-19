# `tranibor-investment-research`: the code of the `investment-research` plugin

A plugin is the one unit a trade ships, with two halves under one name
([Plugins, extensions and editions](../../docs/site/build/plugins-and-trades.md),
[ADR 0181](../../docs/adr/0181-the-contracts-speak-no-trade.md)):

- **its content** (the research campaign template, its two agents and skills, their evals, the setup
  procedures of its connectors) is published from the platform's plugin repository as the plugin
  `investment-research`, and an organisation administrator installs it;
- **its extension**, this distribution, is the code the trade runs inside the platform's processes. The
  API and the workers load it from the `choregos.plugins` entry point `investment-research`
  (`tranibor_investment_research.extension:register`); the published API and worker images install it.

It lives in the core's repository and is released with the core, as its own wheel (ADR 0181 point 5):
until 1.0 freezes the extension seams, a change to a seam and the extension code that calls it land in
one pull request, tested against one commit.

## What it registers

| Seam | What |
| :-- | :-- |
| `tranibor_contracts.CONNECTOR_KINDS.declare` | the connector kind `marketdata`, for this plugin (the contracts' open list, ADR 0181 round C0) |
| `tranibor_adapters.register` | the `marketdata` connector types `stooq`, `yahoo`, `alphavantage`, `tiingo` and `composite` (`marketdata/`), with their specs (`marketdata/specs.py`), and their fake as the kind's `fake` type: `CHOREGOS_FAKES=1` switches every `marketdata` connector to it (ADR 0005), scripted as `marketdata.fake.FAKE` |
| `tranibor_adapters.fakes.scenes.declare_scene` | the eval scene `marketdata/default` the tool-script matrix plays |

## The job image

The template's three job steps (snapshot, profile, backtest; ADR 0056) run `choregos-quant`
(`quant/`, standard library only) in the plugin's job image, `image: investment-research`. The image is
[`Dockerfile`](Dockerfile): the same release's runner image, which runs the step, plus this distribution
installed without its dependencies. Each release builds it FROM the runner it has just published and
signs it as `tranibor-investment-research:<version>` (`.github/workflows/release.yml`, `job-image`);
CI builds it FROM the runner of each pull request that touches the images. The operator maps it:

```yaml
global:
  images:
    jobs: { investment-research: <registry>/tranibor-investment-research@sha256:… }
```

Why here and not in the content's repository: the image carries the runner, which speaks the stage
protocol of its release, and the snapshot calls the `marketdata__get_bars` operation this extension
registers; building both from one tag keeps the three in step. The content names the image, never a
digest, so it is released on its own.

The connector family is documented in [`docs/usage.md`](../../docs/usage.md) ("Market data").

## Tests

`tests/` runs with the core's suite (`uv run pytest plugins/investment-research`): the providers, their
conformance, the key pool, the extension's own registration through its entry point
(`tests/test_extension.py`), the engine (`tests/quant/`), the job image and its release
(`tests/test_job_image.py`), and the published template read from the archive the core ships offline
(`tests/content.py`: its evals, its fan-out, its job steps and agents). The end-to-end campaign is
`tests/e2e/test_m7_investment.py` (`make e2e`), on the core's e2e bench.
