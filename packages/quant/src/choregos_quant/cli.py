# SPDX-License-Identifier: Apache-2.0
"""`choregos-quant` command line: profile, backtest, snapshot.

Exit codes: 0 ok, 2 invalid input, 3 snapshot with too many missing symbols.

Without `--campaign`, the command runs as a Choregos job step (ADR 0056): every path comes from
`CHOREGOS_WORKSPACE` and the results are published in `.choregos/job.json` (see `job.py`).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from choregos_quant.bars import read_bars
from choregos_quant.campaign import load_json, read_campaign
from choregos_quant.engine import backtest_files
from choregos_quant.errors import InputError, SnapshotError
from choregos_quant.job import paths, publish, workspace
from choregos_quant.profile import write_profile
from choregos_quant.snapshot import Fetcher, HttpFetcher, take_snapshot
from choregos_quant.strategies import validate_strategies


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="choregos-quant", description="Deterministic backtest engine")
    sub = p.add_subparsers(dest="command", required=True)
    for name in ("profile", "backtest", "snapshot"):
        s = sub.add_parser(name)
        s.add_argument("--campaign", type=Path, help="campaign JSON; omitted in a job step")
        s.add_argument("--out", type=Path)
        if name != "snapshot":
            s.add_argument("--bars", type=Path)
        if name == "backtest":
            s.add_argument("--strategies", type=Path)
        if name == "snapshot":
            s.add_argument("--tool", default="marketdata__get_bars")
            s.add_argument("--max-missing", type=int, default=0)
            s.add_argument("--previous", type=Path, help="a stored snapshot to reuse; the inputs in a job")
            s.add_argument("--refresh", action="store_true", help="fetch even if a stored snapshot answers")
    return p


def main(argv: list[str] | None = None, fetcher: Fetcher | None = None) -> int:
    args = _parser().parse_args(argv)
    root: Path | None = None
    try:
        if args.campaign is None:
            root = workspace()
            for key, path in paths(args.command, root).items():
                if getattr(args, key, None) is None:
                    setattr(args, key, path)
        for key in ("out", "bars", "strategies"):
            if key in vars(args) and getattr(args, key) is None:
                raise InputError(f"--{key} is required outside a job step")
        campaign = read_campaign(args.campaign)
        if args.command == "profile":
            write_profile(campaign, read_bars(args.bars), args.out)
        elif args.command == "backtest":
            strategies = validate_strategies(
                load_json(args.strategies, "strategies"), campaign.max_strategies
            )
            backtest_files(campaign, read_bars(args.bars), args.bars, args.strategies, strategies, args.out)
        else:
            take_snapshot(
                campaign,
                fetcher or HttpFetcher(args.tool),
                args.out,
                tool=args.tool,
                max_missing=args.max_missing,
                previous=None if args.refresh else args.previous,
            )
        if root is not None:
            publish(args.command, args.out, root)
    except InputError as exc:
        print(f"choregos-quant: invalid input: {exc}", file=sys.stderr)
        return 2
    except SnapshotError as exc:
        print(f"choregos-quant: snapshot incomplete: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
