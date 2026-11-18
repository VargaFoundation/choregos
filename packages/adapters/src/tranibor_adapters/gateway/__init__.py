# SPDX-License-Identifier: Apache-2.0
"""Model gateway adapters."""

from __future__ import annotations

from .litellm import LiteLlmGateway
from .openai_compatible import OpenAiCompatibleGateway

__all__ = ["LiteLlmGateway", "OpenAiCompatibleGateway"]
