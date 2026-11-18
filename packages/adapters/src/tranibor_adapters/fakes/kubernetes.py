# SPDX-License-Identifier: Apache-2.0
"""A Kubernetes API server in memory, reduced to what decides how long a run's Secret lives (#501).

`FakeKubernetesApi` stands for `KubernetesClient` in the `k8s_job` and `tekton` executors. It
answers their REST calls the way the API server does, where it matters for a token's life:

- every created object gets a `uid`; a 404 answers `None`, as `KubernetesClient` does;
- a PUT REPLACES the object (owner references it omits are gone); a PATCH is a JSON merge patch;
- deleting an object collects what it owns (the garbage collector, `propagationPolicy:
  Background`), and `collect(kind, name)` plays any other deletion of an owner: a Job's
  `ttlSecondsAfterFinished`, Tekton's pruner;
- `refuse[(method, path suffix)] = code` fails the next matching call with that code.

Objects are kept by resource (`jobs`, `secrets`, `pipelineruns`, `pods`…) and name; the namespace
is not kept apart — one namespace is what an executor talks to.
"""

from __future__ import annotations

import copy
from typing import Any

from ..errors import UpstreamError


def _merge(target: dict[str, Any], patch: dict[str, Any]) -> None:
    """RFC 7386: a `null` deletes a key, an object merges, anything else replaces."""
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


class FakeKubernetesApi:
    def __init__(self) -> None:
        self.objects: dict[str, dict[str, dict[str, Any]]] = {}
        self.refuse: dict[tuple[str, str], int] = {}
        self.calls: list[tuple[str, str]] = []
        self._uid = 0

    def store(self, resource: str) -> dict[str, dict[str, Any]]:
        return self.objects.setdefault(resource, {})

    @property
    def jobs(self) -> dict[str, dict[str, Any]]:
        return self.store("jobs")

    @property
    def secrets(self) -> dict[str, dict[str, Any]]:
        return self.store("secrets")

    @property
    def pipelineruns(self) -> dict[str, dict[str, Any]]:
        return self.store("pipelineruns")

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        self.calls.append((method, path))
        for (m, suffix), code in list(self.refuse.items()):
            if m == method and path.endswith(suffix):
                del self.refuse[(m, suffix)]
                raise UpstreamError("kubernetes", f"{method} {path} -> {code}", status_code=code)
        parts = path.split("/namespaces/", 1)[-1].split("/")
        if len(parts) < 2:  # a discovery call (`GET /apis/tekton.dev/v1`)
            return {}
        resource, name = parts[1], (parts[2] if len(parts) > 2 else None)
        if len(parts) > 3:  # a subresource (`pods/<name>/log`)
            return "" if name in self.store(resource) else None
        store = self.store(resource)
        body: dict[str, Any] = copy.deepcopy(kwargs.get("json") or {})
        if name is None:
            if method == "POST":
                self._uid += 1
                body["metadata"]["uid"] = f"uid-{self._uid}"
                body.setdefault("status", {})
                store[body["metadata"]["name"]] = body
                return copy.deepcopy(body)
            if method == "GET":
                return {"items": [copy.deepcopy(o) for o in store.values()]}
            raise AssertionError(f"unexpected {method} {path}")
        if name not in store:
            return None
        if method == "GET":
            return copy.deepcopy(store[name])
        if method == "PUT":
            body["metadata"]["uid"] = store[name]["metadata"]["uid"]
            store[name] = body
            return copy.deepcopy(body)
        if method == "PATCH":
            _merge(store[name], body)
            return copy.deepcopy(store[name])
        if method == "DELETE":
            self.collect(resource, name)
            return {}
        raise AssertionError(f"unexpected {method} {path}")

    def collect(self, resource: str, name: str) -> None:
        """The object goes, and with it everything it owns, transitively."""
        uid = self.store(resource).pop(name)["metadata"]["uid"]
        for kind, store in list(self.objects.items()):
            for other, obj in list(store.items()):
                owners = (obj.get("metadata") or {}).get("ownerReferences") or []
                if other in store and any(o.get("uid") == uid for o in owners):
                    self.collect(kind, other)

    def ttl_collects(self, name: str) -> None:
        """A Job's `ttlSecondsAfterFinished` passed: the Job goes, and what it owns."""
        self.collect("jobs", name)
