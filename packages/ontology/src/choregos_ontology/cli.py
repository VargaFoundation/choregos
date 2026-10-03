# SPDX-License-Identifier: Apache-2.0
"""Minimal CLI of the trial: ``validate``, ``compile`` and ``tools`` on a package directory.

Exit codes follow the platform CLI: 0 success, 10 invalid package, 2 usage error.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from choregos_ontology.compiler import compile_directory
from choregos_ontology.validator import Registry

EXIT_OK = 0
EXIT_INVALID = 10


def _registry(args: argparse.Namespace) -> Registry:
    return Registry(sql_engine=args.sql_engine, embeddings=args.embeddings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="choregos-ontology", description=__doc__)
    parser.add_argument("command", choices=["validate", "compile", "tools"])
    parser.add_argument("package", type=Path, help="directory of the ontology package")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--sql-engine", action="store_true", help="a SqlEngine is registered (the data platform)"
    )
    parser.add_argument("--embeddings", action="store_true", help="an embeddings model is configured")
    parser.add_argument("--out", type=Path, help="write the IR here (compile)")
    args = parser.parse_args(argv)

    compiled, validation = compile_directory(args.package, _registry(args))
    if args.command == "validate" or compiled is None:
        if args.json:
            print(json.dumps(validation.to_dict(), indent=2))
        else:
            for issue in [*validation.errors, *validation.warnings]:
                print(issue.format())
            print("valid" if validation.valid else f"invalid: {len(validation.errors)} error(s)")
        return EXIT_OK if validation.valid else EXIT_INVALID
    if args.command == "tools":
        tools = [{"name": t.name, "kind": t.kind, "source": t.source} for t in compiled.mcp_tools]
        print(json.dumps(tools, indent=2) if args.json else "\n".join(t["name"] or "" for t in tools))
        return EXIT_OK
    text = compiled.to_json()
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
