"""CD en mémoire : promotions GitOps, santé, rollouts canary."""

from __future__ import annotations

from choregos_core.domain import Change, Health, PromotionRef, RolloutState, Window


class FakeCd:
    """CD de test. `fail_next_analysis()` casse le prochain canary, pour tester le rollback."""

    def __init__(self) -> None:
        self.revisions: dict[str, str] = {}
        self.promotions: list[tuple[str, list[Change], str]] = []
        self.healths: dict[str, Health] = {}
        self.rollouts: dict[str, RolloutState] = {}
        self.windows: dict[str, list[Window]] = {}
        self.aborted: list[str] = []
        self._fail_analysis = False

    def fail_next_analysis(self) -> None:
        self._fail_analysis = True

    def set_health(self, app: str, status: str, revision: str = "abc123") -> None:
        self.healths[app] = Health(status=status, revision=revision)
        self.revisions[app] = revision

    async def current_revision(self, app: str) -> str:
        return self.revisions.get(app, "unknown")

    async def promote(self, env: str, changes: list[Change], release: str) -> PromotionRef:
        self.promotions.append((env, list(changes), release))
        for change in changes:
            self.revisions[change.app] = change.tag or "promoted"
            self.healths.setdefault(change.app, Health(status="Healthy", revision=change.tag or "promoted"))
        return PromotionRef(
            kind="pr", url=f"https://fake.gitops/pull/{len(self.promotions)}", ref=release, merged=True
        )

    async def health(self, app: str) -> Health:
        return self.healths.get(app, Health(status="Unknown"))

    async def rollout_status(self, app: str) -> RolloutState:
        if self._fail_analysis:
            self._fail_analysis = False
            state = RolloutState(
                phase="Degraded", message="analyse SLO en échec", current_step=1, total_steps=3
            )
        else:
            state = self.rollouts.get(
                app, RolloutState(phase="Healthy", current_step=3, total_steps=3, canary_weight=100)
            )
        self.rollouts[app] = state
        return state

    async def abort_rollout(self, app: str) -> None:
        self.aborted.append(app)
        self.rollouts[app] = RolloutState(phase="Aborted", message="abort demandé")

    async def set_sync_window(self, app: str, windows: list[Window]) -> None:
        self.windows[app] = list(windows)
