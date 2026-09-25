#!/usr/bin/env python3
"""Génère les types TypeScript à partir des JSON Schemas et de l'OpenAPI.

`make contracts` exécute ce script ; `make contracts-check` le rejoue en mode `--check`
et échoue si le résultat diffère de ce qui est committé (le test de non-dérive des contrats).

Le générateur est volontairement écrit à la main (pas de dépendance npm) : la CI doit pouvoir
régénérer les contrats hors ligne, et l'émission reste déterministe.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, ClassVar

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "packages" / "contracts"
SCHEMAS = CONTRACTS / "schemas"
TS_DIR = CONTRACTS / "ts"

HEADER = """/* eslint-disable */
/**
 * Généré par tools/gen_contracts.py — NE PAS MODIFIER À LA MAIN.
 * Source : packages/contracts/{source}
 * Régénérer : make contracts
 */

"""


def pascal(name: str) -> str:
    parts = re.split(r"[^A-Za-z0-9]+", name)
    return "".join(p[:1].upper() + p[1:] for p in parts if p)


class Emitter:
    """Émet du TypeScript à partir d'un sous-schéma JSON Schema 2020-12."""

    def __init__(self, root: dict[str, Any], defs_prefix: str = "", external_prefix: str = "") -> None:
        self.root = root
        self.defs_prefix = defs_prefix
        self.external_prefix = external_prefix
        self.extra: dict[str, str] = {}

    def ref_name(self, ref: str) -> str:
        if ref.startswith("#/$defs/"):
            return self.defs_prefix + pascal(ref.rsplit("/", maxsplit=1)[-1])
        if ref.startswith("#/components/schemas/"):
            return pascal(ref.rsplit("/", maxsplit=1)[-1])
        if ref.startswith("./schemas/") or ref.endswith(".schema.json"):
            base = Path(ref).name.removesuffix(".schema.json")
            return self.external_prefix + pascal(base)
        return "unknown"

    _PRIMITIFS: ClassVar[dict[str, str]] = {
        "null": "null",
        "string": "string",
        "number": "number",
        "integer": "number",
        "boolean": "boolean",
    }

    def type_of(self, schema: Any, indent: int = 0) -> str:
        if schema is False:
            return "never"
        if not isinstance(schema, dict):
            return "unknown"
        if "$ref" in schema:
            return self.ref_name(schema["$ref"])
        if "const" in schema:
            return json.dumps(schema["const"])
        if "enum" in schema:
            return " | ".join(json.dumps(v) for v in schema["enum"])
        return self._compose_of(schema, indent) or self._typed_of(schema, indent)

    def _compose_of(self, schema: dict[str, Any], indent: int) -> str:
        """`oneOf`/`anyOf` → union, `allOf` → intersection ; vide si le schéma n'en a pas."""
        for key in ("oneOf", "anyOf"):
            if key in schema:
                return " | ".join(self.type_of(s, indent) for s in schema[key])
        if "allOf" in schema:
            return " & ".join(self.type_of(s, indent) for s in schema["allOf"])
        return ""

    def _typed_of(self, schema: dict[str, Any], indent: int) -> str:
        t = schema.get("type")
        if isinstance(t, list):
            return " | ".join(self.type_of({**schema, "type": one}, indent) for one in t)
        if t in self._PRIMITIFS:
            return self._PRIMITIFS[str(t)]
        if t == "array":
            return f"Array<{self.type_of(schema.get('items', True), indent)}>"
        if t == "object" or "properties" in schema:
            return self.object_of(schema, indent)
        return "unknown"

    def object_of(self, schema: dict[str, Any], indent: int) -> str:
        props: dict[str, Any] = schema.get("properties", {})
        required = set(schema.get("required", []))
        pad = "  " * (indent + 1)
        lines: list[str] = []
        for name, sub in props.items():
            optional = "" if name in required else "?"
            doc = sub.get("description") if isinstance(sub, dict) else None
            if doc:
                lines.append(f"{pad}/** {doc.strip().splitlines()[0]} */")
            key = name if re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]*", name) else json.dumps(name)
            lines.append(f"{pad}{key}{optional}: {self.type_of(sub, indent + 1)};")
        extra = schema.get("additionalProperties")
        if extra not in (False, None) or not props:
            value = "unknown" if extra in (True, None) else self.type_of(extra, indent + 1)
            lines.append(f"{pad}[key: string]: {value};")
        if not lines:
            return "Record<string, never>"
        close = "  " * indent
        return "{\n" + "\n".join(lines) + f"\n{close}}}"


def emit_schema_file(path: Path) -> tuple[str, list[str]]:
    schema = json.loads(path.read_text(encoding="utf-8"))
    name = pascal(path.name.removesuffix(".schema.json"))
    emitter = Emitter(schema, defs_prefix=name)
    blocks: list[str] = []
    for def_name, def_schema in schema.get("$defs", {}).items():
        ts_name = name + pascal(def_name)
        desc = def_schema.get("description")
        if desc:
            blocks.append(f"/** {desc} */")
        blocks.append(f"export type {ts_name} = {emitter.type_of(def_schema)};\n")
    desc = schema.get("description")
    if desc:
        blocks.append(f"/** {desc} */")
    blocks.append(f"export type {name} = {emitter.type_of(schema)};\n")
    return name, blocks


def generate_schemas_ts() -> str:
    out = [HEADER.format(source="schemas/*.json")]
    names: list[str] = []
    for path in sorted(SCHEMAS.glob("*.json")):
        name, blocks = emit_schema_file(path)
        names.append(name)
        out.extend(blocks)
    out.append("export type ChoregosContract =\n  | " + "\n  | ".join(names) + ";\n")
    return "\n".join(out)


def generate_api_ts() -> str:
    spec = yaml.safe_load((CONTRACTS / "openapi.yaml").read_text(encoding="utf-8"))
    schemas: dict[str, Any] = spec["components"]["schemas"]
    emitter = Emitter(spec, external_prefix="S.")
    out = [HEADER.format(source="openapi.yaml"), 'import type * as S from "./schemas";\n']
    aliases = {
        "StageInput": "S.StageInput",
        "StageResult": "S.StageResult",
        "ContextPack": "S.ContextPack",
        "Finding": "S.Finding",
        "InboundEvent": "S.InboundEvent",
        "ChoregosEvent": "S.Event",
        "HumanDecision": "S.HumanDecision",
    }
    for name, schema in schemas.items():
        ts_name = pascal(name)
        if ts_name in aliases:
            out.append(f"export type {ts_name} = {aliases[ts_name]};\n")
            continue
        desc = schema.get("description")
        if desc:
            out.append(f"/** {desc.strip().splitlines()[0]} */")
        body = emitter.type_of(schema)
        out.append(f"export type {ts_name} = {body};\n")

    # Carte des opérations : opId -> { method, path, response }
    ops: list[str] = []
    for path, item in spec["paths"].items():
        for verb, op in item.items():
            if verb not in {"get", "post", "put", "patch", "delete"}:
                continue
            resp = _response_type(op, emitter)
            body = _body_type(op, emitter)
            ops.append(
                f'  {op["operationId"]}: {{ method: "{verb.upper()}"; path: "{path}";'
                f" body: {body}; response: {resp} }};"
            )
    out.append("export interface Operations {\n" + "\n".join(sorted(ops)) + "\n}\n")
    out.append("export type OperationId = keyof Operations;\n")
    return "\n".join(out)


def _content_type(node: dict[str, Any], emitter: Emitter) -> str:
    content = node.get("content", {})
    for media in ("application/json", "application/problem+json"):
        if media in content:
            return emitter.type_of(content[media].get("schema", True))
    return "void"


def _response_type(op: dict[str, Any], emitter: Emitter) -> str:
    for code, resp in op.get("responses", {}).items():
        if str(code).startswith("2"):
            if isinstance(resp, dict) and "$ref" not in resp:
                return _content_type(resp, emitter)
            return "void"
    return "void"


def _body_type(op: dict[str, Any], emitter: Emitter) -> str:
    body = op.get("requestBody")
    if not isinstance(body, dict):
        return "never"
    return _content_type(body, emitter)


def write(path: Path, content: str, check: bool) -> bool:
    """Écrit le fichier ; en mode check, signale une différence sans écrire."""
    current = path.read_text(encoding="utf-8") if path.exists() else None
    if current == content:
        return False
    if check:
        print(f"[contracts] hors d'état : {path.relative_to(ROOT)}", file=sys.stderr)
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    print(f"[contracts] écrit {path.relative_to(ROOT)}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Génère les types de contrats")
    parser.add_argument("--check", action="store_true", help="échoue si les fichiers générés diffèrent")
    args = parser.parse_args()
    changed = False
    changed |= write(TS_DIR / "schemas.ts", generate_schemas_ts(), args.check)
    changed |= write(TS_DIR / "api.ts", generate_api_ts(), args.check)
    index = HEADER.format(source="schemas/*.json + openapi.yaml") + (
        'export * from "./schemas";\nexport * from "./api";\n'
    )
    changed |= write(TS_DIR / "index.ts", index, args.check)
    if args.check and changed:
        print("[contracts] `make contracts` doit être rejoué et le résultat committé", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
