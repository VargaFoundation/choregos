# SPDX-License-Identifier: Apache-2.0
"""The extension of the `software-delivery` plugin (ADR 0181): what software delivery needs inside the
platform's processes. The plugin's content (agents, skills, procedures) is published from the
publishing identity's agents repository; this distribution is `tranibor-software-delivery`, loaded
from the `choregos.plugins` entry point `software-delivery`."""

from __future__ import annotations

from .names import NAME

__all__ = ["NAME"]
