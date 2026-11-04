# SPDX-License-Identifier: Apache-2.0
"""The Kubernetes quota of a project's namespaces, set by an extension according to the organisation.

Every project has its namespaces (`proj-<slug>-runners`, `proj-<slug>-ci`) and a `ResourceQuota` that
bounds what its runs consume. It was FIXED in `gitops.py` (32 CPU, 96 Gi, 40 pods), the same for
everyone: in multi-organisation, the tenant who pays little got as much as the one who pays a lot,
and nothing allowed to say so.

An extension declares `quota(org, project)`; provisioning plays it when it renders the manifests.
Without an extension, or if the extension returns `None`, the historical default applies. The quota
is written in the GitOps repository: it takes effect at the project's NEXT provisioning, not
retroactively.

The French names this module had before 0.21 (`declarer_un_quota`, `Quota(memoire=...)`...) keep
working until 0.23.0 and warn (ADR 0098 §3 (f)).
"""

from __future__ import annotations

import inspect
import re
import warnings
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .compat import EXTERNAL_ALIAS_REMOVAL, alias_names

_CPU = re.compile(r"^\d+(\.\d+)?m?$")
_MEMORY = re.compile(r"^\d+(Ki|Mi|Gi|Ti)?$")


@dataclass(frozen=True, init=False)
class Quota:
    cpu: str
    memory: str
    pods: int

    def __init__(
        self,
        cpu: str = "32",
        memory: str = "96Gi",
        pods: int = 40,
        *,
        memoire: str | None = None,  # lang: fr-ok alias:external
    ) -> None:
        if memoire is not None:  # lang: fr-ok alias:external
            warnings.warn(
                "Quota(memoire=...) is deprecated: use memory= "  # lang: fr-ok alias:external
                f"(removed in {EXTERNAL_ALIAS_REMOVAL}, ADR 0098)",
                DeprecationWarning,
                stacklevel=2,
            )
            memory = memoire  # lang: fr-ok alias:external
        # A badly written quota only shows when Argo CD refuses to apply it: we refuse here.
        if not _CPU.match(cpu):
            raise ValueError(f"unreadable CPU quota: {cpu!r} (e.g. `8`, `500m`)")
        if not _MEMORY.match(memory):
            raise ValueError(f"unreadable memory quota: {memory!r} (e.g. `16Gi`)")
        if pods < 1:
            raise ValueError("a pods quota is at least 1")
        object.__setattr__(self, "cpu", cpu)
        object.__setattr__(self, "memory", memory)
        object.__setattr__(self, "pods", pods)

    @property
    def memoire(self) -> str:  # lang: fr-ok alias:external
        """Former name of `memory`."""
        return self.memory


DEFAULT = Quota()

QuotaProvider = Callable[[str, str], Awaitable[Quota | None] | Quota | None]
_PROVIDERS: list[QuotaProvider] = []


def declare_quota(provider: QuotaProvider) -> None:
    """Called by an extension when it loads. The first one that returns a quota wins."""
    if provider not in _PROVIDERS:
        _PROVIDERS.append(provider)


async def quota_for(org: str, project: str) -> Quota:
    for provider in _PROVIDERS:
        result = provider(org, project)
        if inspect.isawaitable(result):
            result = await result
        if result is not None:
            return result
    return DEFAULT


def reset() -> None:
    _PROVIDERS.clear()


DEFAUT = DEFAULT  # lang: fr-ok alias:external
Fournisseur = QuotaProvider  # lang: fr-ok alias:external
declarer_un_quota = declare_quota  # lang: fr-ok alias:external
quota_pour = quota_for  # lang: fr-ok alias:external
reinitialiser = reset  # lang: fr-ok alias:external
_FOURNISSEURS = _PROVIDERS  # lang: fr-ok alias:internal

alias_names(
    __name__,
    {
        "DEFAUT": "DEFAULT",  # lang: fr-ok alias:external
        "Fournisseur": "QuotaProvider",  # lang: fr-ok alias:external
        "declarer_un_quota": "declare_quota",  # lang: fr-ok alias:external
        "quota_pour": "quota_for",  # lang: fr-ok alias:external
        "reinitialiser": "reset",  # lang: fr-ok alias:external
    },
    removed_in=EXTERNAL_ALIAS_REMOVAL,
)
