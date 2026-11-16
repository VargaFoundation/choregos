# SPDX-License-Identifier: Apache-2.0
"""Adaptateurs de notification."""

from __future__ import annotations

from .slack import SlackNotifier
from .teams import TeamsNotifier

__all__ = ["SlackNotifier", "TeamsNotifier"]
