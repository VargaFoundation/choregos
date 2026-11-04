# SPDX-License-Identifier: Apache-2.0
"""Execution activities of a step: launch the run, await it, abandon it.

This is the half of `stage` that talks to the executor (Tekton, Kubernetes Job, local Docker,
fake); all idempotent by `run_id`. The preparation (model, key, context) stays in `stage`, the
outcome (cost, result, findings) in `outcome`.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from choregos_adapters.errors import ConfigurationError
from choregos_adapters.executor.log_archive import archive_run_logs
from choregos_api.db.models import Run, RunEvent, WorkItem
from choregos_api.db.session import limit_to_organisations
from choregos_api.security import mint_run_token
from choregos_api.services import persist_event
from choregos_contracts import EventType, StageInput, StageResult, StageStatus
from choregos_core import ExecRef, ExecStatus, StageJobSpec, telemetry, utcnow
from choregos_core.hosted import agent_container
from choregos_core.redaction import REDACTED
from sqlalchemy import select
from temporalio import activity
from temporalio.exceptions import ApplicationError

from ..config import get_settings
from ..gitops import PRIVATE_RANGES
from .base import db, project_bundle, without_secrets
from .outcome import close_run_spend
from .remote import mint_remote_token
from .run_key import mint_run_key

#: The `ApplicationError` type of an executor that refuses a run on its configuration: not retried.
START_REFUSED = "executor_configuration_refused"


def _runner_namespace(settings: Any, slug: str) -> str:
    """Where the pods of a project's agent live. A single rule, read everywhere."""
    return str(settings.runner_namespace_pattern).format(slug=slug)


def hosts_to_reach_directly(settings: Any) -> list[str]:
    """The hosts the runner must reach WITHOUT going through the egress proxy.

    They are not guessed hosts: they are the ones the platform itself gives it: the internal API's
    callback URL, the model gateway, the memory. Reading them here makes the promise of `NO_PROXY`
    true by construction instead of resting on a suffix.

    The suffix was not enough. `.svc` covers `choregos-api.choregos.svc`, NOT `choregos-api`, and
    that is precisely the short form the platform builds itself (`callback_url` ->
    `http://choregos-api:8000`). On the dev tenant on 2026-09-27, the runner's first call, fetching
    its `StageInput`, therefore went to an egress proxy that did not exist in that namespace, and
    died on "Name or service not known". The message named neither the URL nor the proxy: the
    runner concluded "StageInput not found".
    """
    hosts: list[str] = []
    for url in (
        getattr(settings, "callback_url", ""),
        getattr(settings, "gateway_url", ""),
        getattr(settings, "memory_url", ""),
    ):
        host = urlsplit(str(url or "")).hostname
        if host and host not in hosts:
            hosts.append(host)
    return hosts


def runner_environment(settings: Any, namespace: str) -> dict[str, str]:
    """The environment of an agent pod: what the deployment passes, plus the egress proxy.

    `git`, `pip`, `npm`, `uv` and the agent read `HTTP_PROXY`/`HTTPS_PROXY`; `NO_PROXY` keeps the
    cluster's traffic (internal API, gateway, memory) direct: the proxy only serves to get out.
    Without `runner_egress_proxy`, nothing is added.
    """
    env = dict(settings.runner_env)
    if str(getattr(settings, "runner_egress_auth", "") or "") == "run":
        # The runner adds the run id and token to the proxy URL itself, before any child starts.
        env["CHOREGOS_EGRESS_AUTH"] = "run"
    proxy = str(getattr(settings, "runner_egress_proxy", "") or "").format(namespace=namespace)
    if proxy:
        env.setdefault("HTTP_PROXY", proxy)
        env.setdefault("HTTPS_PROXY", proxy)
        env.setdefault(
            "NO_PROXY",
            ",".join(
                (
                    "localhost",
                    "127.0.0.1",
                    ".svc",
                    ".cluster.local",
                    *hosts_to_reach_directly(settings),
                    *PRIVATE_RANGES[:3],
                )
            ),
        )
        for name in ("HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY"):
            env[name.lower()] = env[name]
    return env


def runtime_class(settings: Any, policy: Any) -> str | None:
    """The `RuntimeClass` of an agent pod: the one the policy requires, else the deployment's,
    else none. A `gvisor` policy is not bypassed by the value."""
    if policy.sandbox.runtime == "gvisor":
        return "gvisor"
    return str(getattr(settings, "runner_runtime_class", "") or "") or None


#: How long a run may wait for a slot before being abandoned: six hours, a peak night, not
#: an eternity.
QUEUE_MAX_MINUTES = 360

#: How many polls in a row the executor may answer that it no longer knows a run before the wait
#: ends (S35-02c). One is not enough: an API server hiccup or a claim being replaced is not a lost
#: run. Three polls at the default interval is three minutes, against a stage budget of 35 or more.
LOST_POLLS = 3


async def archive_once(
    executor: Any, run: Run | None, ref: ExecRef, status: ExecStatus | None = None
) -> None:
    """Archive how the run's pod ended and the tail of its log, redacted, once per run (S36-02).

    Called on every way a run ends — result posted, pod finished, timed out, abandoned in the
    queue, cancelled — and always BEFORE the executor is told to cancel, because deleting a Job
    deletes its pods and their logs. Only the `Executor` protocol is used (`status`, `logs`), so a
    new executor gets its runs archived without a line here.

    Idempotent (ADR 0008): the first archive stays; a replayed activity finds it and does nothing.
    Never raises: a run must end even when its logs cannot be read, and the archive says why.
    """
    if run is None or _archived(run):
        return
    with contextlib.suppress(Exception):
        archive = await archive_run_logs(executor, ref, status)
        run.log_archive = archive.model_dump(mode="json")


async def result_from_journal(session: Any, run_id: str) -> StageResult | None:
    """The `StageResult` the runner journaled before posting it (#410), when the post failed.

    The last `run.result` event carrying a `stage_result` wins; one that does not validate is
    ignored rather than trusted. `None` when the runner never got that far.
    """
    rows = (
        (
            await session.execute(
                select(RunEvent)
                .where(RunEvent.run_id == run_id, RunEvent.type == "run.result")
                .order_by(RunEvent.seq.desc())
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        payload = (row.payload or {}).get("stage_result")
        if isinstance(payload, dict):
            with contextlib.suppress(ValueError):
                return StageResult.model_validate(payload)
    return None


def _archived(run: Run) -> bool:
    """A final archive exists; a live snapshot (`final: false`) does not count."""
    return run.log_archive is not None and bool(run.log_archive.get("final", True))


async def snapshot_live(executor: Any, run: Run | None, ref: ExecRef, status: ExecStatus) -> None:
    """While the pod runs, refresh the live tail of its log at each poll (S36-03).

    The API never talks to the cluster: it serves this snapshot, which is as fresh as the poll
    interval (`heartbeat_seconds`). Reading pod logs stays a right of the orchestrator alone.
    A final archive is never overwritten. Never raises.
    """
    if run is None or status.state != "running" or _archived(run):
        return
    with contextlib.suppress(Exception):
        snapshot = await archive_run_logs(executor, ref, status, final=False)
        run.log_archive = snapshot.model_dump(mode="json")


def _capabilities_for(executor: Any, ref: ExecRef, default: list[str]) -> list[str]:
    """What the executor that took THIS run can do: a routing executor has two, the second one lesser."""
    capabilities_for = getattr(executor, "capabilities_for", None)
    return sorted(capabilities_for(ref)) if capabilities_for is not None else default


def _capabilities_of_run(executor: Any, run: Run, namespace: str) -> list[str]:
    ref = ExecRef(
        kind=run.executor_kind or "fake", name=run.executor_ref or run.id, namespace=namespace, run_id=run.id
    )
    return _capabilities_for(executor, ref, sorted(getattr(executor, "capabilities", frozenset())))


@activity.defn(name="start_run")
async def start_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Start the execution (Tekton, Kubernetes Job, local Docker, fake); idempotent by `run_id`."""
    settings = get_settings()
    stage_input = StageInput.model_validate(payload["stage_input"])
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, stage_input.run_id)
        # What the executor can do, for the workflow to decide without assuming (ADR 0013):
        # `suspend` and `resume` make it keep a sandbox across a person's answer (S35-03).
        capabilities = sorted(getattr(bundle.adapters.executor, "capabilities", frozenset()))
        if run is not None and run.executor_ref:
            # A run on the second executor (S40-03c) does what THAT executor does.
            capabilities = _capabilities_of_run(
                bundle.adapters.executor, run, _runner_namespace(settings, bundle.slug)
            )
            return {
                "executor_ref": run.executor_ref,
                "kind": run.executor_kind,
                "reused": True,
                "capabilities": capabilities,
            }
        # The run token lives in the run's Secret only (#519): minted here, never stored, never
        # carried by a Temporal payload. A payload of an older history may still bring one.
        run_token = stage_input.callbacks.run_token
        if not run_token or run_token == REDACTED:
            run_token = mint_run_token(
                stage_input.run_id,
                project_slug=bundle.slug,
                work_item_key=stage_input.work_item.key,
                ttl_minutes=stage_input.budget.max_minutes + 15,
            )
        gateway_key = await _gateway_key(session, bundle, run, stage_input)
        # A remote agent's access token (ADR 0062): exchanged from the organisation's credential,
        # short-lived, for the run's Secret only. The provider rotates the refresh token at every
        # exchange: the new one is committed BEFORE the executor starts, so that a failure after
        # this line cannot lose it.
        remote_token = await mint_remote_token(session, bundle.project.org_id, stage_input)
        if stage_input.remote is not None:
            await session.commit()
            # The scope of the transaction ended with the commit (RLS is transaction-local).
            await limit_to_organisations(session, "*")
        spec = StageJobSpec(
            run_id=stage_input.run_id,
            project_slug=bundle.slug,
            namespace=_runner_namespace(settings, bundle.slug),
            # A job names its image (ADR 0056), an agent written in code too (ADR 0063); prepare_stage
            # resolved each against the operator's map. Neither names a free reference.
            runner_image=(stage_input.job.image if stage_input.job else None)
            or stage_input.agent.image
            or settings.runner_image,
            api_url=stage_input.callbacks.api_url,
            run_token=run_token,
            gateway_key=gateway_key,
            remote_token=remote_token,
            stage_input=stage_input.model_copy(update={"gateway_key": None}),
            timeout_minutes=stage_input.budget.max_minutes + 10,
            runtime_class=runtime_class(settings, bundle.policy),
            env=runner_environment(settings, _runner_namespace(settings, bundle.slug)),
            labels={
                "choregos/project": bundle.slug,
                "choregos/run-id": stage_input.run_id,
                "choregos/work-item": stage_input.work_item.key.replace("/", "_").replace("#", "-"),
            },
            # A hosted agent's container beside the runner (ADR 0080): loopback addresses only.
            agent_container=agent_container(stage_input),
        )
        try:
            ref = await bundle.adapters.executor.start(spec)
        except ConfigurationError as refusal:
            await _fail_run_on_configuration(session, bundle, run, payload, stage_input.run_id, refusal)
            # A refusal that names itself (`hosted_unsupported_cluster`, S41-16) keeps its name.
            kind = getattr(refusal, "code", START_REFUSED)
            raise ApplicationError(str(refusal), type=kind, non_retryable=True) from refusal
        capabilities = _capabilities_for(bundle.adapters.executor, ref, capabilities)
        # An executor that can queue (`queue`) may have created the run WITHOUT a pod, because the
        # concurrency cap was reached. Marking it "running" would make the board, the run page and
        # the measures lie: nothing runs. So it is asked for its state, and only those that can
        # answer are asked.
        in_queue = False
        queue_reason = ""
        if "queue" in getattr(bundle.adapters.executor, "capabilities", frozenset()):
            state = await bundle.adapters.executor.status(ref)
            in_queue = state.state == "pending" and bool(state.message)
            queue_reason = state.message if in_queue else ""
        if run is not None:
            run.executor_kind = str(ref.kind)
            run.executor_ref = ref.name
            run.status = "queued" if in_queue else "running"
        await persist_event(
            session,
            EventType.RUN_QUEUED if in_queue else EventType.RUN_STARTED,
            project_id=bundle.project.id,
            work_item_id=payload["work_item_id"],
            project_slug=bundle.slug,
            subject=stage_input.run_id,
            run_id=stage_input.run_id,
            executor=str(ref.kind),
            **({"reason": queue_reason} if in_queue else {}),
        )
        return {
            "executor_ref": ref.name,
            "kind": str(ref.kind),
            "namespace": ref.namespace,
            "reused": False,
            "queued": in_queue,
            "capabilities": capabilities,
        }


async def _fail_run_on_configuration(
    session: Any, bundle: Any, run: Run | None, payload: dict[str, Any], run_id: str, refusal: Exception
) -> None:
    """The executor refused the run on the deployment's configuration (#750): retrying cannot
    change it, so the run is closed as failed with the message as its reason (`run.result`, which
    the API already serves as `runs[].result`), and the timeline says so. Committed here, because
    the error that follows rolls the session back."""
    message = str(refusal)
    if run is not None:
        run.status = "failed"
        run.ended_at = run.ended_at or utcnow()
        reason = getattr(refusal, "code", START_REFUSED)
        run.result = StageResult.failure(message[:4000], reason).model_dump(mode="json", by_alias=True)
    await persist_event(
        session,
        EventType.RUN_FINISHED,
        project_id=bundle.project.id,
        work_item_id=payload["work_item_id"],
        project_slug=bundle.slug,
        subject=run_id,
        run_id=run_id,
        status="failed",
        reason=message[:300],
    )
    await session.commit()


async def _gateway_key(session: Any, bundle: Any, run: Run | None, stage_input: StageInput) -> str | None:
    """The run's gateway key, for its Secret only (#523): minted now, never stored on the row.

    A run prepared before #523 already has one: on its row (S22-37) or in the payload of an older
    history (before #519). That key is used, and the row's copy leaves the row — the runner reads
    it from its Secret now.

    A job (ADR 0056) calls no model: it gets no key, minted or legacy. Nor does a remote agent
    (ADR 0062): it brings its own model.
    """
    if stage_input.job is not None or stage_input.remote is not None:
        return None
    if run is None:
        return stage_input.gateway_key
    legacy = stage_input.gateway_key or (run.stage_input or {}).get("gateway_key")
    if legacy:
        if run.stage_input and run.stage_input.get("gateway_key") is not None:
            run.stage_input = without_secrets(run.stage_input, keep_gateway_key=False)
        return str(legacy)
    return await mint_run_key(session, bundle, run, stage_input)


@activity.defn(name="await_run")
async def await_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Waits for the run inside its stage span, the root of the run's trace (ADR 0018)."""
    run_id = payload["run_id"]
    async with db() as session:
        run = await session.get(Run, run_id)
        stage_input = (run.stage_input if run else None) or {}
    with telemetry.stage_span(
        run_id,
        stage=run.stage_role if run else None,
        project=(stage_input.get("project") or {}).get("slug"),
        work_item=(stage_input.get("work_item") or {}).get("key"),
    ) as span:
        outcome = await _await_run(payload)
        result = outcome.get("result") or {}
        finished = outcome.get("status") == "finished"
        telemetry.record_outcome(
            span,
            str(result.get("status") if finished else outcome.get("status")),
            failed=not finished or result.get("status") == StageStatus.FAILED.value,
        )
        return outcome


async def _await_run(payload: dict[str, Any]) -> dict[str, Any]:
    """Wait for the end of the run, with heartbeat and clean cancellation (S1-03)."""
    settings = get_settings()
    run_id = payload["run_id"]
    timeout_minutes = float(payload.get("timeout_minutes", 120))
    deadline = timedelta(minutes=timeout_minutes).total_seconds()
    # The time spent IN THE QUEUE does not count against the step's budget: the bench of
    # 2026-09-25 saw two HR runs "exceed 20 min" without a pod ever running, their Job was waiting
    # for a slot. The queue has its own, much wider bound.
    queue_max = timedelta(
        minutes=float(payload.get("queue_timeout_minutes", QUEUE_MAX_MINUTES))
    ).total_seconds()
    waited = 0.0
    in_queue = 0.0
    interval = float(payload.get("poll_seconds", settings.heartbeat_seconds))
    has_run = False
    lost = 0
    while waited <= deadline:
        async with db() as session:
            bundle = await project_bundle(session, payload["project_id"])
            run = await session.get(Run, run_id)
            ref = ExecRef(
                kind=(run.executor_kind if run and run.executor_kind else "fake"),
                name=(run.executor_ref if run and run.executor_ref else run_id),
                # The SAME namespace as at start: without it, the Kubernetes executor queried
                # `/namespaces/None/jobs/...` and got a 403 whose message speaks of rights, never of
                # the missing namespace.
                namespace=_runner_namespace(settings, bundle.slug),
                run_id=run_id,
            )
            if run is not None and run.result is not None:
                await archive_once(bundle.adapters.executor, run, ref)
                return {"status": "finished", "result": run.result}
            status = await bundle.adapters.executor.status(ref)
            await snapshot_live(bundle.adapters.executor, run, ref, status)
            # While it waits, its token ages. Minted at preparation for `max_minutes + 15`, it
            # expires DURING the wait if the queue is long, and the agent starts to receive
            # "Signature has expired" (seen on the bench of 2026-09-24). It is replaced at every
            # poll, as long as nothing has started.
            if (
                run is not None
                and run.status == "queued"
                and status.state == "pending"
                and "renew" in getattr(bundle.adapters.executor, "capabilities", frozenset())
            ):
                run_item = await session.get(WorkItem, run.work_item_id)
                renew = getattr(bundle.adapters.executor, "renew", None)
                if renew is not None and run_item is not None:
                    # A failed renewal does not kill the run: it may still start in time, and if it
                    # cannot it will fail on ITS own message. A missing right must degrade, not
                    # destroy; this is the second time that lesson is paid for (bench of 2026-09-24).
                    with contextlib.suppress(Exception):
                        await renew(
                            ref,
                            mint_run_token(
                                run_id,
                                project_slug=bundle.slug,
                                work_item_key=run_item.tracker_key,
                                # Enough to cover what is left of the wait, plus the step.
                                ttl_minutes=int(timeout_minutes) + 15,
                            ),
                        )
            # The run was waiting for a slot and has just got it: without this switch, it would
            # stay "queued" until its end, although it runs. It shows here, because this is where the
            # executor is queried.
            if run is not None and run.status == "queued" and status.state == "running":
                run.status = "running"
                run.started_at = run.started_at or utcnow()
                await persist_event(
                    session,
                    EventType.RUN_STARTED,
                    project_id=bundle.project.id,
                    work_item_id=run.work_item_id,
                    project_slug=bundle.slug,
                    subject=run_id,
                    run_id=run_id,
                    executor=str(ref.kind),
                )
        if status.finished:
            async with db() as session:
                bundle = await project_bundle(session, payload["project_id"])
                run = await session.get(Run, run_id)
                await archive_once(bundle.adapters.executor, run, ref, status)
                if run is not None and run.result is not None:
                    return {"status": "finished", "result": run.result}
                # The runner could not post: we read the result published by the executor
                # (`result-url` on the Tekton side), and record it ourselves.
                # First from the run's journal, which the API already holds (#410): the runner
                # journals the whole result before posting it, on every executor.
                fetched = await result_from_journal(session, run_id)
                fetcher = getattr(bundle.adapters.executor, "fetch_result", None)
                if fetched is None and fetcher is not None:
                    fetched = await fetcher(ref)
                if fetched is not None:
                    payload_result = fetched.model_dump(mode="json", by_alias=True)
                    if run is not None:
                        run.result = payload_result
                    return {"status": "finished", "result": payload_result}
            if status.state == "succeeded":
                return {
                    "status": "missing_result",
                    "result": StageResult(
                        status=StageStatus.FAILED,
                        summary="the runner ended without posting a result",
                        reason="invalid_result",
                    ).model_dump(mode="json", by_alias=True),
                }
            return {
                "status": status.state,
                "result": StageResult(
                    status=StageStatus.FAILED,
                    summary=status.message or f"run {status.state}",
                    reason=status.state,
                ).model_dump(mode="json", by_alias=True),
            }
        # The executor no longer knows the run: its claim, Job or container is gone. Nothing will
        # bring it back, and `unknown` is not `finished`: the wait went on to the end of the stage
        # budget, then started over on each Temporal retry. On the dev tenant, on 2026-10-18, a
        # review whose claim had been collected (`ttlSecondsAfterFinished`) while the executor was
        # being redeployed waited 1 h 32 for a 35 min budget, with nothing running.
        lost = lost + 1 if status.state == "unknown" else 0
        if lost >= LOST_POLLS:
            return await _lost_run(payload["project_id"], run_id, ref, status)
        with contextlib.suppress(RuntimeError):  # hors contexte Temporal (tests unitaires)
            activity.heartbeat({"run_id": run_id, "waited_s": waited, "en_file_s": in_queue})
        await asyncio.sleep(interval)
        if status.state == "pending" and not has_run:
            in_queue += interval
            if in_queue > queue_max:
                await _abandon(payload["project_id"], run_id, ref)
                return {
                    "status": "queue_timed_out",
                    "result": StageResult(
                        status=StageStatus.FAILED,
                        summary=(
                            f"never admitted: {in_queue / 60:.0f} min in the queue "
                            f"({status.message or 'limit reached'})"
                        ),
                        reason="queue_timeout",
                    ).model_dump(mode="json", by_alias=True),
                }
            continue
        has_run = True
        waited += interval
    # A run that exceeds its budget must leave nothing behind: a suspended Job whose state nobody
    # reads any more stays at the head of the queue and blocks all the following ones (seen on the
    # bench of 2026-09-25, five work items frozen behind two dead runs).
    await _abandon(payload["project_id"], run_id, ref)
    return {
        "status": "timed_out",
        "result": StageResult(
            status=StageStatus.FAILED, summary=f"over {timeout_minutes:g} min", reason="timeout"
        ).model_dump(mode="json", by_alias=True),
    }


async def _lost_run(project_id: str, run_id: str, ref: ExecRef, status: ExecStatus) -> dict[str, Any]:
    """Ends the wait for a run the executor no longer knows (S35-02c).

    Its journal first: a claim collected after the runner journaled its result lost nothing.
    """
    async with db() as session:
        run = await session.get(Run, run_id)
        fetched = await result_from_journal(session, run_id)
        if fetched is not None:
            payload_result = fetched.model_dump(mode="json", by_alias=True)
            if run is not None:
                run.result = payload_result
            return {"status": "finished", "result": payload_result}
    await _abandon(project_id, run_id, ref)
    return {
        "status": "lost",
        "result": StageResult(
            status=StageStatus.FAILED,
            summary=f"the executor no longer knows this run ({status.message or 'not found'})",
            reason="lost",
        ).model_dump(mode="json", by_alias=True),
    }


async def _abandon(project_id: str, run_id: str, ref: ExecRef) -> None:
    """Remove the Job (or the equivalent) of a run that is no longer awaited, never raising: what
    matters is that the run ends; the cleanup is a duty, not a condition."""
    with contextlib.suppress(Exception):
        async with db() as session:
            bundle = await project_bundle(session, project_id)
            run = await session.get(Run, run_id)
            # Before the cancel: deleting the Job deletes its pods, and their logs with them.
            await archive_once(bundle.adapters.executor, run, ref)
            await bundle.adapters.executor.cancel(ref)
            if run is not None and run.status in {"queued", "running"}:
                run.status = "failed"
                run.ended_at = utcnow()


async def abandon_queued_runs(project_id: str, run_ids: list[str]) -> None:
    """Removes, at the executor, the runs among these that still wait for a slot (#501).

    A queued run is a suspended Job (`k8s_job`) or a Secret marked `choregos/queued`
    (`agent_sandbox`). Nothing ends it on its own: a suspended Job never finishes, so its
    `ttlSecondsAfterFinished` never starts, and Kubernetes stops `activeDeadlineSeconds` while a
    Job is suspended. Only `await_run` gave it a bound (`queue_timeout_minutes`); once the
    interpreter dies, nobody polls it, and the Job and its Secret (token, gateway key) stayed
    forever. Deleting the Job (propagation `Background`) takes the Secret it owns with it.

    Only runs the executor knows (`executor_ref`) and that never ran (`queued`): a running run
    ends on its own deadline, and its TTL then collects it. Never raises.
    """
    for run_id in run_ids:
        with contextlib.suppress(Exception):
            async with db() as session:
                bundle = await project_bundle(session, project_id)
                run = await session.get(Run, run_id)
                if run is None or run.status != "queued" or not run.executor_ref:
                    continue
                ref = ExecRef(
                    kind=run.executor_kind or "fake",
                    name=run.executor_ref,
                    namespace=_runner_namespace(get_settings(), bundle.slug),
                    run_id=run.id,
                )
            await _abandon(project_id, run_id, ref)


@activity.defn(name="cancel_run")
async def cancel_run(payload: dict[str, Any]) -> None:
    """Stops a run nobody will wait for — its interpreter was cancelled — and closes its key.

    Replayable (ADR 0008): cancelling what is gone is not an error, and the spend is booked once.
    Its key is revoked and scrubbed after the executor is told to stop (#523), never before: a
    revoked key may no longer tell what it spent.
    """
    async with db() as session:
        bundle = await project_bundle(session, payload["project_id"])
        run = await session.get(Run, payload["run_id"])
        if run is None:
            return
        ref = ExecRef(
            kind=run.executor_kind or "fake",
            name=run.executor_ref or run.id,
            namespace=_runner_namespace(get_settings(), bundle.slug),
            run_id=run.id,
        )
        await archive_once(bundle.adapters.executor, run, ref)
        with contextlib.suppress(Exception):  # gone already, or never started
            await bundle.adapters.executor.cancel(ref)
        if run.status in {"queued", "running"}:  # a run that ended keeps how it ended
            run.status = "cancelled"
        run.ended_at = run.ended_at or utcnow()
    await close_run_spend(payload["project_id"], payload["run_id"])
