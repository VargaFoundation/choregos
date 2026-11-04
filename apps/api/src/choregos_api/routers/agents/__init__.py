# SPDX-License-Identifier: Apache-2.0
"""The agent registry of the organisation (ADR 0033).

An agent is an object of the organisation, internal or external; what it IS lives in its versions,
which never change — a change publishes the next one. A project pins a version and can
only tighten it: a lower budget, fewer tools. An override that widens gets a 422.

The routes live in one module per concern, all registered on the router of `_router`. FastAPI matches
routes in registration order, so the import order below is the route order (S47-29): do not sort it.
"""

from __future__ import annotations

from ._router import AgentRef, Slug, router

from . import registry, versions, metrics, lifecycle, pins, trials, credentials, catalogue  # isort: skip  # noqa: F401

__all__ = ["AgentRef", "Slug", "router"]
