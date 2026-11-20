# SPDX-License-Identifier: Apache-2.0
"""`extract`: the files of `.choregos/inputs/` as one document of normalised text (ADR 0198 point 3).

Extraction does not judge: a sentence in an offer that talks to its reader is text like any other, kept
unchanged. What it measures is what could not be read, and the workflow's gate decides what to do.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from tranibor_procurement import convert as conv
from tranibor_procurement.numbers import settle, settle_table

DEFAULT_MAX_CHARS: Final = 150_000
OUTPUT_NAME: Final = "offers_text"
NOT_A_FILE: Final = "not_a_regular_file"


@dataclass
class Extraction:
    document: dict[str, Any]
    facts: dict[str, int]
    unreadable: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        f = self.facts
        line = f"tranibor-procurement extract: {f['files_read']} of {f['files_total']} file(s) read"
        if f["files_truncated"]:
            line += f", {f['files_truncated']} truncated"
        if f["notation_ambiguous"]:
            line += f", {f['notation_ambiguous']} with an ambiguous number notation"
        if self.unreadable:
            line += "; unreadable: " + ", ".join(f"{name} ({reason})" for name, reason in self.unreadable)
        return line[:3500]


def input_files(inputs: Path) -> list[Path]:
    """The files handed to the step, by name; a hidden file and a folder are not files."""
    return sorted(p for p in inputs.iterdir() if not p.name.startswith(".") and not p.is_dir())


def extract(
    inputs: Path,
    *,
    number_format: str = "auto",
    timeout: float = conv.DEFAULT_TIMEOUT_S,
    max_chars: int = DEFAULT_MAX_CHARS,
    child: Sequence[str] | None = None,
) -> Extraction:
    files: list[dict[str, Any]] = []
    unreadable: list[tuple[str, str]] = []
    truncated = ambiguous = 0
    paths = input_files(inputs)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            unreadable.append((path.name, NOT_A_FILE))
            continue
        done = conv.convert_file(path, timeout, command=child)
        if isinstance(done, conv.Unreadable):
            unreadable.append((path.name, done.reason))
            continue
        text, notation = (settle_table if done.kind == "csv" else settle)(done.markdown, number_format)
        if notation == "ambiguous":
            ambiguous += 1
        if len(text) > max_chars:
            truncated += 1
        files.append(
            {
                "name": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "type": done.kind,
                "pages": done.pages,
                "chars": len(text),
                "notation": notation,
                "text": text[:max_chars],
            }
        )
    facts = {
        "files_total": len(paths),
        "files_read": len(files),
        "files_unreadable": len(unreadable),
        "files_truncated": truncated,
        "notation_ambiguous": ambiguous,
    }
    return Extraction({"files": files}, facts, unreadable)
