# SPDX-License-Identifier: Apache-2.0
"""`tranibor-procurement extract|compare|tier|supplier-check|renewals`, job steps (ADR 0198).

Exit codes: 0 the contract is kept (files may be unreadable: see the facts), 2 it is broken.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from decimal import Decimal

from tranibor_procurement import job
from tranibor_procurement.compare import compare
from tranibor_procurement.convert import DEFAULT_TIMEOUT_S
from tranibor_procurement.extract import DEFAULT_MAX_CHARS, OUTPUT_NAME, extract
from tranibor_procurement.numbers import NUMBER_FORMATS
from tranibor_procurement.relay import HttpRelay, Relay
from tranibor_procurement.renewals import MAX_CEILING, renewals
from tranibor_procurement.supplier_check import parse_as_of, supplier_check
from tranibor_procurement.tier import parse_threshold, tier


def _positive(text: str) -> float:
    value = float(text)
    if value <= 0:
        raise argparse.ArgumentTypeError("must be above zero")
    return value


def _threshold(text: str) -> Decimal:
    try:
        return parse_threshold(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _as_of(text: str) -> dt.date:
    try:
        return parse_as_of(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="tranibor-procurement", description="Deterministic steps of the procurement plugin"
    )
    sub = p.add_subparsers(dest="command", required=True)
    e = sub.add_parser("extract", help="the files of .choregos/inputs/ as one JSON document of text")
    e.add_argument("--timeout", type=_positive, default=DEFAULT_TIMEOUT_S, help="seconds per file")
    e.add_argument("--max-chars-per-file", type=int, default=DEFAULT_MAX_CHARS, help="text kept per file")
    sub.add_parser("compare", help="the numbers of a comparison, computed from the structured offers")
    t = sub.add_parser("tier", help="the approval tier of an award basis")
    t.add_argument(
        "--committee-from",
        type=_threshold,
        required=True,
        metavar="AMOUNT",
        help="the highest total from which the committee decides",
    )
    c = sub.add_parser("supplier-check", help="the measured facts of a supplier's onboarding file")
    c.add_argument(
        "--required",
        nargs="+",
        action="extend",
        required=True,
        metavar="KIND",
        help="a kind of document the file must hold",
    )
    c.add_argument("--as-of", type=_as_of, required=True, metavar="DATE", help="an ISO date, or `today`")
    c.add_argument("--duplicates", action="store_true", help="look for the supplier in the purchasing system")
    r = sub.add_parser("renewals", help="the contracts that end soon, for a renewal campaign")
    r.add_argument(
        "--within-days", type=int, required=True, metavar="N", help="contracts ending within N days"
    )
    r.add_argument("--max", type=int, default=50, help=f"the most contracts listed (at most {MAX_CEILING})")
    r.add_argument(
        "--skip-decided",
        action="store_true",
        help="leave out a contract with a `Renewal decision:` note dated within --within-days days",
    )
    return p


def main(argv: list[str] | None = None, relay: Relay | None = None) -> int:
    """`relay` is for tests; a job step reaches the purchasing connector through the run's API."""
    args = _parser().parse_args(argv)
    try:
        root = job.workspace()
        if args.command == "compare":
            summary, facts, outputs = compare(root)
            job.publish(root, summary=summary, facts=facts, outputs=outputs)
            return 0
        if args.command == "tier":
            summary, facts, outputs = tier(root, args.committee_from)
            job.publish(root, summary=summary, facts=facts, outputs=outputs)
            return 0
        if args.command == "supplier-check":
            summary, facts, outputs = supplier_check(
                root, args.required, args.as_of, relay=(relay or HttpRelay()) if args.duplicates else None
            )
            job.publish(root, summary=summary, facts=facts, outputs=outputs)
            return 0
        if args.command == "renewals":
            summary, facts, outputs = renewals(
                relay or HttpRelay(), args.within_days, args.max, skip_decided=args.skip_decided
            )
            job.publish(root, summary=summary, facts=facts, outputs=outputs)
            return 0
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
