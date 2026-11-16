# SPDX-License-Identifier: Apache-2.0
"""Scriptable fakes of what an outpost talks to (ADR 0005): the platform's door."""

from .door import FakeCall, FakeDoor, FakeInstance

__all__ = ["FakeCall", "FakeDoor", "FakeInstance"]
