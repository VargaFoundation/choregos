# SPDX-License-Identifier: Apache-2.0
"""Configuration des workers Temporal."""

from __future__ import annotations

from functools import lru_cache

from choregos_core.images import check_image_map
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

QUEUE_ORCHESTRATOR = "orchestrator"
QUEUE_EXECUTOR = "executor"
QUEUE_TRACKER = "tracker"
QUEUE_MEMORY = "memory"
ALL_QUEUES = (QUEUE_ORCHESTRATOR, QUEUE_EXECUTOR, QUEUE_TRACKER, QUEUE_MEMORY)


class OrchestratorSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHOREGOS_", env_file=".env", extra="ignore")

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    fakes: bool = Field(default=False, validation_alias="CHOREGOS_FAKES")
    api_url: str = "http://localhost:8000"
    # The URL the AGENTS call back, from inside the cluster. It is not the public URL: a pod does
    # not resolve an ingress's name, and the runner died on "Name or service not known" with no
    # message naming the URL. It had existed here from the start and was read nowhere: the
    # callbacks took `api_url`.
    internal_api_url: str = "http://localhost:8000"
    gateway_url: str = "http://localhost:4000"
    gateway_type: str = "litellm"
    #: The memory (Ecphoria). The chart has always passed it (`CHOREGOS_MEMORY_URL` of
    #: `commonEnv`) and this setting did not exist: `extra="ignore"` swallowed it in silence, and
    #: the memory's MCP URL was built by `gateway_url.replace("4000", "8432")`. The replacement
    #: only holds if the gateway and the memory share a host. On the dev tenant, the gateway is
    #: `http://litellm:4000`: the memory became `http://litellm:8432`, a closed port on the right
    #: host for the wrong service. Measured on 2026-09-27: no answer in eight seconds, where
    #: `http://ecphoria:8432/mcp` answers 401 in nine milliseconds.
    memory_url: str = "http://localhost:8432"
    #: The address of the tool catalogue's MCP server, as the agent sees it. It is LOCAL to the
    #: agent pod, and someone always serves it: a `sidecar:` under Tekton
    #: (`templates/github-tekton-argo-k8s/tekton/agent-stage.yaml`), the runner itself everywhere
    #: else (`runner/local_tools.py`, which starts the server if nothing answers).
    tools_mcp_url: str = "http://localhost:7777/mcp"
    runner_image: str = "ghcr.io/vargafoundation/choregos-runner:1.0.0"
    #: The images a job step may name (ADR 0056), as JSON in `CHOREGOS_JOB_IMAGES`: name → reference,
    #: pinned by digest. A workflow names an image, never pulls one: the operator decides what runs.
    #: Each is built FROM the runner image — the runner is what runs the job's command.
    job_images: dict[str, str] = {}
    #: The images an agent written in code may name (ADR 0063), as JSON in `CHOREGOS_AGENT_IMAGES`:
    #: same rules as `job_images`, and the same variable the API validates a version against.
    agent_images: dict[str, str] = {}
    #: A reference without `@sha256:` stops the worker at start-up (`CHOREGOS_REQUIRE_IMAGE_DIGEST`).
    require_image_digest: bool = False
    executor_kind: str = "agent_sandbox"
    # Variables passed to EVERY agent pod (JSON in `CHOREGOS_RUNNER_ENV`). What is secret has no
    # business here: `CHOREGOS_RUNNER_ENV_SECRETS` lists secrets, which the executor mounts by
    # reference.
    runner_env: dict[str, str] = {}
    runner_namespace_pattern: str = "proj-{slug}-runners"
    # The proxy an agent pod goes out through (`HTTP_PROXY`/`HTTPS_PROXY`), with `{namespace}` for
    # the runners' one: provisioning deploys a Squid there whose allowlist is the project policy's.
    # Empty: no proxy, as on the local bench, whose namespace has neither NetworkPolicy nor proxy.
    runner_egress_proxy: str = ""
    #: `run`: the runner presents its run id and run token to the egress proxy as proxy credentials
    #: (ADR 0070): the per-run `choregos-egress` proxy of the chart sets it. Empty: no credentials.
    runner_egress_auth: str = ""
    #: Hosts no run of this deployment may reach (comma-separated host patterns, ADR 0070): the same
    #: value as the API's `CHOREGOS_NETWORK_INSTANCE_DENY`.
    network_instance_deny: str = ""
    # This proxy's image; empty: the one `gitops.py` knows. To pin by digest where Kyverno
    # requires it.
    egress_image: str = ""
    # The `RuntimeClass` of EVERY agent pod when the project policy imposes none
    # (`sandbox.runtime: gvisor` wins). Empty: the cluster's default runtime. A cluster that has
    # gVisor or Kata says so here once, rather than project by project.
    runner_runtime_class: str = ""
    #: Below this reservation (USD), a stage does not start: its key would be refused at its first
    #: model call (ADR 0047, S27-02). `CHOREGOS_MIN_RUN_RESERVATION_USD`.
    min_run_reservation_usd: float = Field(default=0.01, ge=0)

    #: The deployment's kill switch for context reduction (ADR 0055): `off` turns rtk and headroom
    #: off for every run, whatever a policy asks. `on` (the default) lets the policy decide, and a
    #: policy without `context` reduces nothing.
    context_reduction: bool = True
    #: The deployment's kill switch for tool scripts (ADR 0122 §6): off (the default) offers no run the
    #: tool bridge, whatever a policy asks; `on` lets the organisation's ceiling and the policy decide,
    #: and a policy without `tools` offers nothing. `CHOREGOS_TOOL_SCRIPTS`.
    tool_scripts: bool = False

    @model_validator(mode="after")
    def _images_named_by_the_operator(self) -> OrchestratorSettings:
        check_image_map("CHOREGOS_JOB_IMAGES", self.job_images, require_digest=self.require_image_digest)
        check_image_map("CHOREGOS_AGENT_IMAGES", self.agent_images, require_digest=self.require_image_digest)
        return self

    @property
    def callback_url(self) -> str:
        """What the runner receives in `callbacks.api_url`, without the internal API suffix."""
        return (self.internal_api_url or self.api_url).rstrip("/").removesuffix("/api/v1/internal")

    @property
    def tools_sidecar_url(self) -> str:
        """The URL of the tools MCP server, announced to the agent: always, and that is correct.

        I briefly made this announcement conditional on a sidecar, believing no server existed
        under the `k8s_job` executor. That was wrong: `runner/local_tools.py` starts this server
        itself when the announced address is local and nothing answers, and its header describes
        exactly the defect I thought I had found, fixed before me. The condition therefore removed
        a TRUE announcement, and deprived the agent of its tools.

        The contract is simple, and it holds for every executor: the platform announces a local
        address, and someone serves it: a sidecar under Tekton, the runner everywhere else. A
        NON-local address is served by no one automatically: it is up to the deployment to provide
        the server, and `start_if_absent` then abstains, on purpose.
        """
        return self.tools_mcp_url

    object_store_url: str = "s3://choregos"
    public_url: str = "http://localhost:3000"
    log_level: str = "INFO"
    log_json: bool = True
    fx_usd_eur: float = 0.92
    history_size_threshold: int = 20_000
    heartbeat_seconds: int = 60
    # The tracker's fallback polling (S3-06): 0 disables the catch-up.
    reconcile_interval_seconds: int = 60


@lru_cache(maxsize=1)
def get_settings() -> OrchestratorSettings:
    return OrchestratorSettings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
