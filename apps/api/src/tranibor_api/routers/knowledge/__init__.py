# SPDX-License-Identifier: Apache-2.0
"""Knowledge bases of the organisation (ADR 0051).

A knowledge base is a curated corpus a human chose; its documents are uploaded here, answered 202
`pending`, and converted to markdown in the background (`knowledge.conversion`), never inside the
request. Curating is the organisation administrator's (`agent:manage`), or the owners' of the project that
owns the base (`project:write`, S39-14), who may also widen or narrow its reach (ADR 0081, amended).
Reading follows the base's scope (`knowledge.access`).

The routes live in one module per concern, all registered on the router of `_router`. FastAPI matches
routes in registration order, so the import order below is the route order (S47-30): do not sort it.
"""

from __future__ import annotations

from ._router import router

from . import bases, grants, documents, search, sources, distillation  # isort: skip  # noqa: F401

__all__ = ["router"]
