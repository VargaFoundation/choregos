# SPDX-License-Identifier: Apache-2.0
"""`ReleaseTrain`: one singleton per project x environment (docs/plan/05 §5.2).

The train is the second of the three locks: the merge queue keeps `main` green, the train keeps
the environment, and the declarative guard rails (Argo windows, GitHub Environments) hold even if
Choregos goes down.
"""

from __future__ import annotations

import contextlib
from collections import deque
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from .activity_names import named
from .scheduling import execute_on_its_queue, start_on_its_queue

with workflow.unsafe.imports_passed_through():
    from choregos_contracts import InboundEventType, ReleaseStatus

    from ..activities import train as train_activities

DEFAULT_RETRY = RetryPolicy(maximum_attempts=5, initial_interval=timedelta(seconds=2))
NO_RETRY = RetryPolicy(maximum_attempts=1)
#: An `auto_sync` environment warns its tickets (#278, S21-27). Before this marker, the train waited
#: there for `abort` without reading anything, and a ticket that boarded there waited 72 h and then a
#: human.
AUTO_SYNC_WARNS_ITS_ITEMS = "auto-sync-previent"  # lang: fr-ok — a patch id is never renamed
#: The ticket whose signal STARTS the train (signal-with-start) stays in the batch (S22-22). Before
#: this marker, `run` overwrote the batch with the carried-over tickets: on the dev tenant, on 9
#: October, #4 and #6 each woke their own train and were erased from it at once: the train waited,
#: empty, and the ticket waited for a delivery that would never come.
START_SIGNAL_BOARDS = "start-signal-boards"
#: An express departure takes ITS lane's approval (`express_lane.approval`, #279, S22-17). Before
#: this marker, it took the ordinary approval: the express lane was never read.
EXPRESS_LANE_APPROVAL = "express-lane-approval"
#: The express lane is recognised by ITS label (`express_lane.label`, #351, S22-30), and a ticket already
#: on board that comes back carrying it triggers the express lane. Before this marker, `merged` compared
#: against a hard-coded `hotfix` (a project that named its lane `urgent` never took it, and a `hotfix`
#: still did) and the duplicate branch merged the approval then returned without reading the labels.
#: `patched` is only called where the old reading and the new one diverge (a label other than `hotfix`,
#: or a duplicate carrying the label): a pre-change run replaying a history where nothing diverges never
#: meets it, and takes the fix if it is updated before then.
EXPRESS_LANE_READS_ITS_LABEL = "express-lane-reads-its-label"
#: An express boarding whose lane says `skip_schedule: false` waits for the next scheduled departure (the
#: cron is due inside an open window) instead of leaving at once (#351, S22-30). Before this marker,
#: `skip_schedule` was read nowhere and the express lane always bypassed the schedule. `patched` is only
#: called when the train would otherwise leave against the schedule; a held express that relays carries
#: `express: True` into the next run.
EXPRESS_LANE_HONOURS_SKIP_SCHEDULE = "express-lane-honours-skip-schedule"
#: The express lane label when the policy names none (the default of the `ExpressLane` contract).
DEFAULT_EXPRESS_LABEL = "hotfix"
#: The idle train relays (continue-as-new) before Temporal kills it (S22-24). Before this marker,
#: only a departure led to the relay: a train without a batch looped in `_collect`, one
#: `check_window` activity and one timer per turn, ~11 events per minute. On the dev tenant, on 10
#: October, `train-dev-staging` held 15,814 events after 23 h (1,438 `check_window`), Temporal was
#: already suggesting the relay, and at 51,200 events it terminates the workflow (~78 h): the train
#: dies, and the tickets that board it wait forever.
#: `patched` is only called once the threshold is crossed: it is memoised per execution, and an
#: early call while replaying an old history would return `False` for the whole life of the run. So
#: a run started on the previous version takes the fix if it is updated before the threshold.
IDLE_TRAIN_CONTINUES_AS_NEW = "idle-train-continues-as-new"
#: The train relays BEFORE its history gets too long to replay (S22-27). Before this marker, the
#: threshold was 15,000 events: on the dev tenant, on 10 October (0.17.11), a history of ~16,000
#: events (2.6 MB) took 32 to 34 s to replay on the orchestrator pod (1 core):
#: `[TMPRL1104] ... workflow_task_duration=33943 workflow_history_size=2571325`. But the workflow
#: task times out at 10 s: after a `temporal workflow reset`, or after any worker restart
#: (deployment, sticky cache lost), every task timed out (`WORKFLOW_TASK_TIMED_OUT`) and the train
#: no longer progressed; every `status_query` from the console cost a full 33 s replay and ended in
#: "query task not found, or already expired" (API 500).
#: Same discipline as `IDLE_TRAIN_CONTINUES_AS_NEW`: the threshold first, `patched` second. A
#: previous run that replays beyond the new threshold calls `patched` there with no marker:
#: `False`, memoised, and it keeps the previous relay (`FORMER_RELAY_THRESHOLD`, under the old
#: marker). Lowering the threshold without a new marker did not replay: the old marker, queried as
#: soon as the new threshold was crossed, returned `False` for the whole life of the run, and the
#: relay it had recorded at 15,000 became a determinism gap (TMPRL1100, verified).
TRAIN_CONTINUES_AS_NEW_EARLY = "train-continues-as-new-early"
#: A departure requested on a train WITHOUT A BATCH is dropped (#370, S22-29). Before this marker,
#: `depart_now` (or `express`) raised a flag that only `_depart` resets; but `_collect` does not depart
#: without a batch, and its wait (`depart_requested or express or abort_requested`) returned at once:
#: the train chained `check_window` activities without a pause until the next boarding or the relay.
#: Choice: the request is NOT carried over to the next item that boards. A manual departure is a
#: deliberate act on a visible batch (it overrides the window and the cron); carrying it over would
#: send, hours later, an item nobody chose to send outside its window. The relay already behaves that
#: way (it does not carry `depart_requested`). The API still answers 202: the signal is accepted, the
#: train ignores it for want of a batch. `express` follows the same rule (a hotfix only raises it by
#: boarding, hence with a batch: a theoretical case, covered at no cost).
#: Same discipline as `IDLE_TRAIN_CONTINUES_AS_NEW`: the condition first, `patched` second.
EMPTY_TRAIN_DROPS_DEPARTURE = "empty-train-drops-departure"
#: A FROZEN train no longer spins on a pending departure (#515, S22-29). Before this marker, a frozen
#: train with a batch and `depart_requested` (or `express`) skipped both exits of `_collect` (both
#: require `not self.frozen`), and its wait returned at once: `check_window` back to back until the
#: unfreeze or the relay.
#: Choice: a MANUAL departure requested while frozen is dropped, not kept for the unfreeze. The freeze
#: is a deliberate stop and the unfreeze a deliberate restart: a departure clicked before it (possibly
#: hours before, by someone else) leaving the instant the train is unfrozen would be a surprise; whoever
#: unfreezes can ask again. `express` is KEPT: it is not a request but a property of the batch (a hotfix
#: boarded), cleared only by a departure; it simply stops waking the wait while frozen, and the unfreeze
#: wakes it, so the hotfix leaves as express as soon as the train is unfrozen, as before the freeze.
#: Same discipline: the condition first, `patched` second.
FROZEN_TRAIN_DOES_NOT_SPIN = "frozen-train-does-not-spin"
#: A `freeze` handled BEFORE the body of `run` (a start signal, or one right behind the start) is kept
#: (#763, S22-36b). Before this marker `run` did `self.frozen = params.frozen`, which overwrote it while
#: `freeze_reason` stayed: a train showing a reason but not frozen, which could depart. `unfreeze`,
#: `abort`, `express` and `depart_now` were checked: `run` never overwrites them (`express` is only
#: raised, never lowered), so only the freeze needed the marker. An `unfreeze` before the body of a
#: train relayed frozen is still lost (one bit cannot tell it from the relay's own `frozen`): the
#: train stays frozen, the safe side, and whoever unfreezes sends it again.
FREEZE_BEFORE_RUN_IS_KEPT = "freeze-before-run-is-kept"
#: The threshold (in history events) beyond which the train relays: idle, after a departure and
#: after an `auto_sync` sync. 2,000: replay costs ~2 ms per event on the pod (33.9 s for 16,000), so
#: ~4 s, under the workflow task's 10 s with a margin for a CPU contended by the console's
#: queries. At rest (~11 events per minute), that is a relay every ~3 h.
#: A train can receive it in its input (`seuil_de_relais`, a recorded input): an archived history
#: that relays earlier then replays against the code as it is, without redefining anything.
RELAY_THRESHOLD = 2_000
#: The threshold from before S22-27, which only the runs that did not take
#: `TRAIN_CONTINUES_AS_NEW_EARLY` replay.
FORMER_RELAY_THRESHOLD = 15_000
#: How many times, and at what pace, the environment is looked at before concluding.
AUTO_SYNC_ATTEMPTS = 30
AUTO_SYNC_PAUSE = timedelta(minutes=2)
#: The train reads what Argo CD announces to it (#280, S22-18). Before this marker, `deploy_event`
#: stored the events in `events` without anything reading them: only the polling of the
#: departure's steps saw a degradation, at its next pass.
TRAIN_LISTENS_TO_CD = "train-listens-to-cd"
#: What, announced for the departure's environment, makes it roll back without waiting for polling.
CD_ALERTS = frozenset({str(InboundEventType.CD_DEGRADED), str(InboundEventType.ROLLOUT_ABORTED)})


@dataclass
class TrainInput:
    project_slug: str
    env: str
    project_id: str | None = None
    carried_items: list[dict[str, Any]] = field(default_factory=list)
    batch_no: int = 1
    frozen: bool = False
    #: `None`: the code's threshold (`RELAY_THRESHOLD`, and the former one for earlier runs). A given
    #: threshold applies to the idle relay, from before as from after S22-27.
    seuil_de_relais: int | None = None  # lang: fr-ok — a recorded continue-as-new input (ADR 0098)
    #: The English name of `seuil_de_relais` (S45-03): read first, the former one after. Continue-as-new
    #: inputs recorded before S45-03 carry only the former name; S45-39 writes this one.
    relay_threshold: int | None = None
    #: An express departure held by the schedule (`skip_schedule: false`) when the train relayed.
    express: bool = False

    def threshold(self) -> int:
        """This train's relay threshold: the one its input gives, under either name, else the code's."""
        if self.relay_threshold is not None:
            return self.relay_threshold
        if self.seuil_de_relais is not None:
            return self.seuil_de_relais
        return RELAY_THRESHOLD

    def former_idle_threshold(self) -> int:
        """The idle relay threshold as S22-24 read it (a run that did not take S22-27)."""
        given = self.seuil_de_relais if self.relay_threshold is None else self.relay_threshold
        return FORMER_RELAY_THRESHOLD if given is None else given


@workflow.defn(name="ReleaseTrain", sandboxed=False)
class ReleaseTrain:
    """Collection, departure, staging, soak, approval, canary, verification, rollback, freeze."""

    def __init__(self) -> None:
        self.batch: list[dict[str, Any]] = []
        self.events: deque[dict[str, Any]] = deque()
        self.status: str = str(ReleaseStatus.COLLECTING)
        self.frozen: bool = False
        self.freeze_reason: str | None = None
        self.depart_requested: bool = False
        self.express: bool = False
        self.approved: bool | None = None
        self.approved_by: str | None = None
        self.abort_requested: bool = False
        self.batch_no: int = 1
        self.current_release: str | None = None
        self.next_departure: str | None = None
        self.window_open: bool = True
        self.listens_to_cd: bool = False
        #: `None` until the configuration is loaded: boardings received before it (the signal that
        #: starts the train, those delivered during `load_train_config`) wait in `pending_labels`, and
        #: the express lane examines them once its label is known.
        self.express_label: str | None = None
        self.pending_labels: list[tuple[list[str], bool]] = []
        #: A human asked for the departure (`depart_now`): it bypasses the schedule, express or not.
        self.manual_depart: bool = False

    # ───────────────────────── signals ─────────────────────────

    @workflow.signal
    def merged(self, payload: dict[str, Any]) -> None:
        """A merged ticket boards the train. A duplicate counts once, but the approval it brings is
        added: the webhook boards the ticket without it, the interpreter with it."""
        labels = [str(label) for label in payload.get("labels") or []]
        duplicate = False
        for item in self.batch:
            if item.get("work_item_key") == payload.get("work_item_key"):
                item["approval"] = _combine_approvals(item.get("approval"), payload.get("approval"))
                # The labels of every boarding: the release lists those set between two boardings.
                # An activity input, not a command: replaying an older history is unaffected.
                if labels:
                    item["labels"] = list(dict.fromkeys([*(item.get("labels") or []), *labels]))
                duplicate = True
                break
        if not duplicate:
            self.batch.append(payload)
        if self.express_label is None:
            self.pending_labels.append((labels, duplicate))
        else:
            self._check_express_lane(labels, duplicate=duplicate)

    def _check_express_lane(self, labels: list[str], *, duplicate: bool) -> None:
        """A boarding that carries the express lane label requests an express departure.

        Before `EXPRESS_LANE_READS_ITS_LABEL`, only a first boarding labelled `hotfix` counted. The
        condition first, `patched` second: the marker is only read when the decision changes."""
        configured = self.express_label or DEFAULT_EXPRESS_LABEL
        before = not duplicate and DEFAULT_EXPRESS_LABEL in labels
        after = configured in labels
        express = before
        if after != before:
            express = after if workflow.patched(EXPRESS_LANE_READS_ITS_LABEL) else before
        if express:
            self.express = True
            self.depart_requested = True

    @workflow.signal
    def depart_now(self, payload: dict[str, Any]) -> None:
        self.depart_requested = True
        self.manual_depart = True

    @workflow.signal
    def freeze(self, payload: dict[str, Any]) -> None:
        self.frozen = True
        self.freeze_reason = str(payload.get("reason", ""))

    @workflow.signal
    def unfreeze(self, payload: dict[str, Any]) -> None:
        self.frozen = False
        self.freeze_reason = None

    @workflow.signal
    def approve(self, payload: dict[str, Any]) -> None:
        self.approved = True
        self.approved_by = str(payload.get("by", ""))

    @workflow.signal
    def reject(self, payload: dict[str, Any]) -> None:
        self.approved = False
        self.approved_by = str(payload.get("by", ""))

    @workflow.signal
    def abort(self, payload: dict[str, Any]) -> None:
        self.abort_requested = True

    @workflow.signal
    def deploy_event(self, payload: dict[str, Any]) -> None:
        """An Argo CD event for this environment (webhook `/webhooks/argocd`). The departure reads it
        during the soak, the canary and the verification (`_watched_by_the_cd`)."""
        self.events.append(payload)

    @workflow.query
    def status_query(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "batch_size": len(self.batch),
            "pending_items": [item.get("work_item_key", "") for item in self.batch],
            "frozen": self.frozen,
            "freeze_reason": self.freeze_reason,
            "next_departure": self.next_departure,
            "window_open": self.window_open,
            "release_id": self.current_release,
        }

    # ───────────────────────── loop ─────────────────────────

    @workflow.run
    async def run(self, payload: dict[str, Any]) -> dict[str, Any]:
        params = TrainInput(**payload)
        # A held express is carried by ONE relay; the next relays say it again only if still held.
        payload = {key: value for key, value in payload.items() if key != "express"}
        # The signal that starts the train is delivered BEFORE this body: the batch already holds the ticket.
        already_boarded, self.batch = list(self.batch), list(params.carried_items)
        if workflow.patched(START_SIGNAL_BOARDS):
            for item in already_boarded:
                self.merged(item)
        self.batch_no = params.batch_no
        if workflow.patched(FREEZE_BEFORE_RUN_IS_KEPT):
            self.frozen = params.frozen or self.frozen
        else:
            self.frozen = params.frozen
        if params.express:
            self.express = True
            self.depart_requested = True

        config = await execute_on_its_queue(
            train_activities.load_train_config,
            {"project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(seconds=30),
            retry_policy=DEFAULT_RETRY,
        )
        self.express_label = str((config.get("express_lane") or {}).get("label") or DEFAULT_EXPRESS_LABEL)
        pending, self.pending_labels = self.pending_labels, []
        for labels, duplicate in pending:
            self._check_express_lane(labels, duplicate=duplicate)
        if config.get("mode") == "auto_sync":
            # No train: Argo follows `main`. The workflow stays alive for queries.
            self.status = "auto_sync"
            if not workflow.patched(AUTO_SYNC_WARNS_ITS_ITEMS):
                await _wait(lambda: self.abort_requested)
                return {"status": "auto_sync"}
            await self._follow_the_sync(params, payload)
            return {"status": "auto_sync"}

        departures = 0
        while departures < 20 and not self.abort_requested:
            await self._collect(config, params, payload)
            if self.abort_requested:
                break
            released = await self._depart(params, config)
            departures += 1
            if released.get("frozen"):
                self.frozen = True
                self.freeze_reason = released.get("reason", "rollback")
            if self._relays_early(params) or _history_length() > FORMER_RELAY_THRESHOLD:
                workflow.continue_as_new(
                    {
                        **payload,
                        "carried_items": self.batch,
                        "batch_no": self.batch_no,
                        "frozen": self.frozen,
                    }
                )
        return {"status": self.status, "batches": departures}

    def _express_held_by_schedule(self, config: dict[str, Any], *, scheduled: bool) -> bool:
        """An express departure waits for the schedule when its lane says `skip_schedule: false`.

        A human departure (`depart_now`) is never held. The condition first, `patched` second (see
        `EXPRESS_LANE_HONOURS_SKIP_SCHEDULE`)."""
        if not self.express or self.manual_depart or scheduled:
            return False
        if (config.get("express_lane") or {}).get("skip_schedule", True):
            return False
        return workflow.patched(EXPRESS_LANE_HONOURS_SKIP_SCHEDULE)

    def _relays_early(self, params: TrainInput) -> bool:
        """The threshold is crossed (or Temporal suggests the relay) AND the run took S22-27.

        `patched` is only called once the condition is true (see `TRAIN_CONTINUES_AS_NEW_EARLY`). It
        only returns `True` to a caller that relays at once: the three callers do."""
        return (
            _history_length() > params.threshold() or workflow.info().is_continue_as_new_suggested()
        ) and workflow.patched(TRAIN_CONTINUES_AS_NEW_EARLY)

    async def _follow_the_sync(self, params: TrainInput, payload: dict[str, Any]) -> None:
        """`auto_sync`: each ticket that boards waits for the environment to be healthy, then is warned."""
        while not self.abort_requested:
            await _wait(lambda: bool(self.batch) or self.abort_requested)
            if self.abort_requested:
                return
            items, self.batch = list(self.batch), []
            sync_id = f"auto-sync-{params.env}-{self.batch_no}"
            self.batch_no += 1
            for attempt in range(AUTO_SYNC_ATTEMPTS):
                verdict = await execute_on_its_queue(
                    named(train_activities.confirm_auto_sync, train_activities.confirmer_auto_sync),
                    {
                        "project_slug": params.project_slug,
                        "env": params.env,
                        "items": [str(item.get("work_item_key") or "") for item in items],
                        "sync_id": sync_id,
                        # Recorded payload key (ADR 0098 §4): never renamed.
                        "conclure": attempt == AUTO_SYNC_ATTEMPTS - 1,
                    },
                    start_to_close_timeout=timedelta(minutes=2),
                    retry_policy=DEFAULT_RETRY,
                )
                if verdict.get("ok") is not None or self.abort_requested:
                    break
                await _wait(lambda: self.abort_requested, AUTO_SYNC_PAUSE)
            if self._relays_early(params) or _history_length() > FORMER_RELAY_THRESHOLD:
                workflow.continue_as_new(
                    {**payload, "carried_items": self.batch, "batch_no": self.batch_no, "frozen": self.frozen}
                )

    async def _collect(self, config: dict[str, Any], params: TrainInput, payload: dict[str, Any]) -> None:
        """Wait for the cron, a full batch, a manual departure or a hotfix, and relay when idle."""
        self.status = str(ReleaseStatus.COLLECTING)
        batch_max = int(config.get("batch_max", 8))
        while True:
            window = await execute_on_its_queue(
                train_activities.check_window,
                {"config": config},
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=DEFAULT_RETRY,
            )
            self.window_open = bool(window.get("open", True))
            self.next_departure = window.get("next_departure")
            wait_s = float(window.get("wait_seconds", 300))
            ready = len(self.batch) >= batch_max or (
                self.batch and self.window_open and window.get("due", False)
            )
            # A departure requested by a human (or a hotfix) overrides the window and the cron: it
            # is a deliberate act, traced in the audit. Only the freeze holds it back.
            held = False
            if (self.express or self.depart_requested) and self.batch and not self.frozen:
                held = self._express_held_by_schedule(
                    config, scheduled=bool(self.window_open and window.get("due", False))
                )
                if not held:
                    return
            if ready and not self.frozen and len(self.batch) >= int(config.get("batch_min", 1)):
                return
            # At rest, the history grows with no departure to relay it: the train relays here, its
            # batch with it. The threshold first, `patched` second (see
            # `IDLE_TRAIN_CONTINUES_AS_NEW`); a run from before S22-27 keeps the former threshold,
            # under its marker.
            if not self.abort_requested and (
                self._relays_early(params)
                or (
                    (
                        _history_length() > params.former_idle_threshold()
                        or workflow.info().is_continue_as_new_suggested()
                    )
                    and workflow.patched(IDLE_TRAIN_CONTINUES_AS_NEW)
                )
            ):
                workflow.continue_as_new(
                    {
                        **payload,
                        "carried_items": self.batch,
                        "batch_no": self.batch_no,
                        "frozen": self.frozen,
                        **({"express": True} if held else {}),
                    }
                )
            # A departure requested without a batch does not leave (above): drop it here, or the
            # wait would return at once and the next turn would rerun `check_window` without a pause.
            if (
                not self.batch
                and (self.depart_requested or self.express)
                and workflow.patched(EMPTY_TRAIN_DROPS_DEPARTURE)
            ):
                self.depart_requested = False
                self.express = False
                self.manual_depart = False
            # A frozen train does not leave (above): a manual request is dropped, `express` is kept
            # but no longer wakes the wait until the unfreeze (see `FROZEN_TRAIN_DOES_NOT_SPIN`).
            held_by_freeze = (
                self.frozen
                and (self.depart_requested or self.express)
                and workflow.patched(FROZEN_TRAIN_DOES_NOT_SPIN)
            )
            if held_by_freeze:
                self.depart_requested = False
                self.manual_depart = False
                await _wait(
                    lambda: (
                        self.depart_requested or (self.express and not self.frozen) or self.abort_requested
                    ),
                    timedelta(seconds=max(5.0, min(wait_s, 900.0))),
                )
            elif held:
                # An express held by the schedule (`skip_schedule: false`, never frozen nor empty):
                # only a human departure wakes it; the next turn checks the schedule again.
                await _wait(
                    lambda: self.manual_depart or self.abort_requested,
                    timedelta(seconds=max(5.0, min(wait_s, 900.0))),
                )
            else:
                await _wait(
                    lambda: self.depart_requested or self.express or self.abort_requested,
                    timedelta(seconds=max(5.0, min(wait_s, 900.0))),
                )
            if self.abort_requested:
                return

    async def _depart(self, params: TrainInput, config: dict[str, Any]) -> dict[str, Any]:
        """A departure: promotion, soak, approval, canary, verification, possible rollback."""
        express = self.express
        self.express = False
        self.depart_requested = False
        self.manual_depart = False
        if self.frozen:
            self.status = str(ReleaseStatus.FROZEN)
            await _wait(lambda: not self.frozen or self.abort_requested)
            return {"frozen": self.frozen}

        items = list(self.batch)
        self.batch = []
        self.status = str(ReleaseStatus.DEPARTING)
        self.listens_to_cd = workflow.patched(TRAIN_LISTENS_TO_CD)
        if self.listens_to_cd:
            # What Argo CD announced during the collection is about the previous release: this
            # departure does not roll back for it (the smoke test looks at the real state right after).
            self.events.clear()
        release = await execute_on_its_queue(
            train_activities.create_release,
            {
                "project_slug": params.project_slug,
                "env": params.env,
                "batch_no": self.batch_no,
                "items": items,
                "express": express,
            },
            start_to_close_timeout=timedelta(minutes=2),
            retry_policy=DEFAULT_RETRY,
        )
        self.current_release = release["release_id"]
        self.batch_no += 1

        promoted = await execute_on_its_queue(
            train_activities.promote,
            {"release_id": self.current_release, "project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=DEFAULT_RETRY,
        )
        if not promoted.get("ok"):
            return await self._rollback(params, config, "promotion failed")

        self.status = str(ReleaseStatus.STAGING)
        soak_minutes = int(
            (config.get("express_soak_minutes") if express else config.get("soak_minutes")) or 10
        )
        smoke = await execute_on_its_queue(
            train_activities.run_smoke,
            {"release_id": self.current_release, "project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(minutes=15),
            retry_policy=NO_RETRY,
        )
        if not smoke.get("ok"):
            return await self._rollback(params, config, "smoke tests failed")

        soak = await self._watched_by_the_cd(
            params,
            config,
            train_activities.soak,
            {
                "release_id": self.current_release,
                "project_slug": params.project_slug,
                "env": params.env,
                "minutes": soak_minutes,
            },
            start_to_close_timeout=timedelta(minutes=soak_minutes + 10),
            retry_policy=NO_RETRY,
        )
        if not soak.get("ok"):
            # The soak's polling keeps its former reason; an Argo CD alert gives its own.
            reason = soak["reason"] if soak.get("argocd") else "SLOs degraded during the soak"
            return await self._rollback(params, config, reason)

        # The policy's approval, OR the one a ticket of the batch brings from its workflow (ADR
        # 0041): a fix leaves alone, a feature waits for its captain. A boarding from before does
        # not carry `approval`; the computation then returns the policy alone, as before.
        policy = config.get("approval") or {}
        lane = (config.get("express_lane") or {}).get("approval")
        # An express departure takes its lane's approval INSTEAD of the ordinary one; the batch
        # tickets' requirement still holds. A lane that says nothing keeps the ordinary one.
        if express and lane is not None and workflow.patched(EXPRESS_LANE_APPROVAL):
            policy = lane
        approval = _departure_approval(policy, items)
        if approval.get("required"):
            self.status = str(ReleaseStatus.AWAITING_APPROVAL)
            self.approved = None
            await execute_on_its_queue(
                train_activities.request_approval,
                {
                    "release_id": self.current_release,
                    "project_slug": params.project_slug,
                    "env": params.env,
                    "group": approval.get("group"),
                },
                start_to_close_timeout=timedelta(minutes=2),
                retry_policy=DEFAULT_RETRY,
            )
            timeout = timedelta(hours=int(approval.get("timeout_hours", 4)))
            got = await _wait(lambda: self.approved is not None or self.abort_requested, timeout)
            if self.abort_requested or self.approved is False or not got:
                self.batch = items + self.batch  # the batch leaves again next turn
                self.status = str(ReleaseStatus.COLLECTING)
                await execute_on_its_queue(
                    train_activities.mark_release,
                    {
                        "release_id": self.current_release,
                        "status": "collecting",
                        "reason": "approval refused",
                    },
                    start_to_close_timeout=timedelta(minutes=1),
                    retry_policy=DEFAULT_RETRY,
                )
                return {"frozen": False, "reason": "approval refused"}

        # Infra goes before code: applying Terraform after the canary would amount to sending
        # code to production onto an infra that is not expecting it yet.
        terraform = await execute_on_its_queue(
            train_activities.apply_terraform,
            {
                "release_id": self.current_release,
                "project_slug": params.project_slug,
                "env": params.env,
            },
            start_to_close_timeout=timedelta(minutes=45),
            heartbeat_timeout=timedelta(minutes=5),
            retry_policy=NO_RETRY,
        )
        if terraform.get("ok") is False:
            return await self._rollback(params, config, terraform.get("reason", "Terraform apply failed"))

        self.status = str(ReleaseStatus.PROMOTING)
        canary = config.get("canary") or {}
        steps = list(canary.get("steps", [100]))
        step_minutes = list(canary.get("step_minutes", [0] * len(steps)))
        for index, weight in enumerate(steps):
            analysis = await self._watched_by_the_cd(
                params,
                config,
                train_activities.promote_canary_step,
                {
                    "release_id": self.current_release,
                    "project_slug": params.project_slug,
                    "env": params.env,
                    "weight": weight,
                    "minutes": step_minutes[index] if index < len(step_minutes) else 0,
                },
                start_to_close_timeout=timedelta(
                    minutes=(step_minutes[index] if index < len(step_minutes) else 0) + 15
                ),
                retry_policy=NO_RETRY,
            )
            if not analysis.get("ok"):
                return await self._rollback(params, config, analysis.get("reason", "canary analysis failed"))

        self.status = str(ReleaseStatus.VERIFYING)
        verdict = await self._watched_by_the_cd(
            params,
            config,
            train_activities.verify_prod,
            {"release_id": self.current_release, "project_slug": params.project_slug, "env": params.env},
            start_to_close_timeout=timedelta(minutes=30),
            retry_policy=NO_RETRY,
        )
        if not verdict.get("ok"):
            return await self._rollback(params, config, verdict.get("reason", "post-deployment check failed"))

        self.status = str(ReleaseStatus.DONE)
        await execute_on_its_queue(
            train_activities.finish_release,
            {
                "release_id": self.current_release,
                "project_slug": params.project_slug,
                "env": params.env,
                "approved_by": self.approved_by,
            },
            start_to_close_timeout=timedelta(minutes=5),
            retry_policy=DEFAULT_RETRY,
        )
        return {"frozen": False, "release_id": self.current_release}

    async def _watched_by_the_cd(
        self, params: TrainInput, config: dict[str, Any], activity: Any, arg: Any, **options: Any
    ) -> Any:
        """A departure step (soak, canary tier, verification) that Argo CD can interrupt.

        The step's polling remains the net. A `cd.app.degraded` or a `cd.rollout.aborted` received
        for the departure's environment and applications gets ahead of it: the step is cancelled and
        returns a negative verdict, which the caller treats as its own (rollback). The events read
        are consumed: none is read again by the next step or the next departure.
        """
        if not self.listens_to_cd:
            return await execute_on_its_queue(activity, arg, **options)
        apps = [str(app) for app in config.get("apps") or []]
        step = start_on_its_queue(activity, arg, **options)
        await workflow.wait_condition(
            lambda: step.done() or any(_alert(e, params.env, apps) for e in self.events)
        )
        reason = self._take_the_alert(params.env, apps)
        if reason is None:
            return await step
        if not step.done():
            step.cancel()
        # The cancelled step (or one that failed at the same time) says nothing more: the Argo CD
        # alert decides.
        with contextlib.suppress(ActivityError):
            await step
        return {"ok": False, "reason": reason, "argocd": True}

    def _take_the_alert(self, env: str, apps: list[str]) -> str | None:
        """Consume the events up to the first alert that concerns this departure, and return its reason.

        Those of another environment or another application are dropped on the way; what follows the
        alert is about the same incident, which the rollback handles: everything is consumed."""
        while self.events:
            reason = _alert(self.events.popleft(), env, apps)
            if reason is not None:
                self.events.clear()
                return reason
        return None

    async def _rollback(self, params: TrainInput, config: dict[str, Any], reason: str) -> dict[str, Any]:
        """Rollback: undo, mark, notify, freeze if the policy asks."""
        self.status = str(ReleaseStatus.ROLLED_BACK)
        await execute_on_its_queue(
            train_activities.rollback,
            {
                "release_id": self.current_release,
                "project_slug": params.project_slug,
                "env": params.env,
                "reason": reason,
            },
            start_to_close_timeout=timedelta(minutes=10),
            retry_policy=DEFAULT_RETRY,
        )
        if config.get("freeze_on_rollback", True):
            self.frozen = True
            self.freeze_reason = reason
            self.status = str(ReleaseStatus.FROZEN)
        return {"frozen": self.frozen, "reason": reason}


def _alert(event: dict[str, Any], env: str, apps: list[str]) -> str | None:
    """The rollback's reason if the event is an Argo CD alert for this environment and one of its
    applications; `None` otherwise. An event that names neither environment nor application counts,
    as for the ticket (ADR 0041): an event carried by hand keeps its effect."""
    if str(event.get("type") or "") not in CD_ALERTS:
        return None
    detail = event.get("payload") or {}
    if detail.get("env") and str(detail["env"]) != env:
        return None
    app = str(detail.get("app") or "")
    if app and apps and app not in apps:
        return None
    subject = app or env
    if str(event.get("type")) == str(InboundEventType.ROLLOUT_ABORTED):
        return f"Argo CD reported the {subject} rollout aborted"
    return f"Argo CD reported {subject} degraded"


def _combine_approvals(first: dict[str, Any] | None, second: dict[str, Any] | None) -> dict[str, Any] | None:
    """Two approval requirements of the same ticket: required if either requires it; the first
    group named wins."""
    if not first or not second:
        return first or second
    return {
        "required": bool(first.get("required") or second.get("required")),
        "group": first.get("group") or second.get("group"),
    }


def _departure_approval(policy: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, Any]:
    """Required if the policy OR a ticket of the batch requires it. The group: the policy's if it is
    named, otherwise that of the first ticket that names one; the delay stays the policy's."""
    requirements = [i["approval"] for i in items if (i.get("approval") or {}).get("required")]
    if not requirements:
        return policy
    group_name = policy.get("group") or next((e.get("group") for e in requirements if e.get("group")), None)
    return {**policy, "required": True, "group": group_name}


def _history_length() -> int:
    """The length of the run's history, in events."""
    return workflow.info().get_current_history_length()


async def _wait(condition: Any, timeout: timedelta | None = None) -> bool:  # noqa: ASYNC109
    """A `wait_condition` that returns `False` on expiry rather than raising."""
    if timeout is None:
        await workflow.wait_condition(condition)
        return True
    try:
        await workflow.wait_condition(condition, timeout=timeout)
    except TimeoutError:
        return False
    return True
