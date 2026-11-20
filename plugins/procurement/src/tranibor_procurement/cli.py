# SPDX-License-Identifier: Apache-2.0
"""`tranibor-procurement extract [--timeout N] [--max-chars-per-file N]`, a job step (ADR 0198).

Exit codes: 0 the contract is kept (files may be unreadable: see the facts), 2 it is broken.
"""

from __future__ import annotations

import argparse
import sys

from tranibor_procurement import job
from tranibor_procurement.convert import DEFAULT_TIMEOUT_S
from tranibor_procurement.extract import DEFAULT_MAX_CHARS, OUTPUT_NAME, extract
from tranibor_procurement.numbers import NUMBER_FORMATS


def _positive(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be above zero")
    return value


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tranibor-procurement", description="Deterministic steps of the procurement plugin"
    )
    sub = p.add_subparsers(dest="command", required=True)
    e = sub.add_parser("extract", help="the files of .choregos/inputs/ as one JSON document of text")
    e.add_argument("--timeout", type=_positive, default=DEFAULT_TIMEOUT_S, help="seconds per file")
    e.add_argument("--max-chars-per-file", type=int, default=DEFAULT_MAX_CHARS, help="text kept per file")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        root = job.workspace()
        inputs = job.inputs_dir(root)
        number_format = str(job.read_fields(root).get("number_format") or "auto")
        if number_format not in NUMBER_FORMATS:
            raise job.ContractError(
                f"the field number_format must be one of {', '.join(NUMBER_FORMATS)}, not {number_format!r}"
            )
        done = extract(
            inputs,
            number_format=number_format,
            timeout=args.timeout,
            max_chars=max(args.max_chars_per_file, 0),
        )
        job.publish(root, summary=done.summary(), facts=done.facts, outputs={OUTPUT_NAME: done.document})
    except job.ContractError as exc:
        print(f"tranibor-procurement: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
