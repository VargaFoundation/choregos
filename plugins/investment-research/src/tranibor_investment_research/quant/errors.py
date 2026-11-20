# SPDX-License-Identifier: Apache-2.0
"""Errors of the package; the CLI maps them to exit codes."""

from __future__ import annotations


class InputError(ValueError):
    """Invalid input file (campaign, bars, strategies): exit code 2."""


class SnapshotError(RuntimeError):
    """Too many symbols could not be fetched: exit code 3."""
