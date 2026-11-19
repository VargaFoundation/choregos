# SPDX-License-Identifier: Apache-2.0
"""Deterministic backtest engine: standard library only, no LLM, byte-identical outputs.

The `choregos-quant` command the `investment-research` template's job steps run, in the plugin's job image
(`plugins/investment-research/Dockerfile`, ADR 0181 O4-3). It imports nothing outside the standard library
and this package: the job image installs the distribution without its dependencies.
"""

__all__ = ["__version__"]
__version__ = "0.17.16"
