# SPDX-License-Identifier: Apache-2.0
"""API configuration (12-factor, `CHOREGOS_` prefix)."""

from __future__ import annotations

import ipaddress
from functools import lru_cache
from typing import Any, Literal

from choregos_core.images import check_image_map
from choregos_core.mcp import RESULT_CEILING_BYTES, RESULT_MAX_BYTES
from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: What a Go template engine writes when the variable does not exist: it renders this string
#: literally instead of failing. Helm and the Infisical operator both do it.
UNRESOLVED_TEMPLATE = ("<no value>", "<nil>")

#: The session secret the code ships with. It is published in the repository, so no deployed
#: instance may sign with it (`security.check_session_secret`).
DEFAULT_SESSION_SECRET = "dev-session-secret-change-me"
#: The shortest session secret a deployed instance accepts: a 256-bit HMAC key.
MIN_SESSION_SECRET_BYTES = 32


#: The clients of the Integrations page that a pre-registered OAuth client can serve (ADR 0030).
OAUTH_CLIENTS = frozenset(
    {"claude-code", "claude-desktop", "claude-ai", "cursor", "vscode", "chatgpt", "other"}
)


class OAuthClient(BaseModel):
    """A client that the IdP registered for the MCP door: the Integrations page derives its command."""

    client_id: str = Field(min_length=1)
    #: The port of the local redirect (`http://localhost:PORT/callback`) that the IdP registered,
    #: for a client that receives the code on the workstation (Claude Code).
    callback_port: int | None = Field(default=None, ge=1024, le=65535)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CHOREGOS_", env_file=".env", extra="ignore")

    env: Literal["dev", "staging", "prod", "test"] = "dev"
    # Listening port. In a container, 8000; on a development machine, 8000 is often already taken,
    # and `choregos-api` exited on "Address already in use" with no way to change it other than
    # editing the code.
    port: int = 8000
    debug: bool = False
    fakes: bool = Field(default=False, validation_alias="CHOREGOS_FAKES")
    #: The edition that the DEPLOYMENT announces (ADR 0024). It is not the same thing as the edition
    #: that runs: that one is declared by the enterprise extension when it loads. The two are
    #: compared at startup, because a gap is silent otherwise: the chart would announce
    #: `enterprise`, the image would be community, everything would start, and the first second
    #: organisation would be refused without anyone understanding why.
    edition: Literal["community", "enterprise"] = "community"

    # Database
    database_url: str = "sqlite+aiosqlite:///./choregos.db"
    db_echo: bool = False
    #: Connection pool, BOUNDED. `pool_size` alone let SQLAlchemy open ten more overflow
    #: connections, and wait thirty seconds for a free one (inventory of 2026-09-24). Each process
    #: holds at most `db_pool_size + db_max_overflow` connections; `db_pool_recycle_s` closes those
    #: that a firewall or PgBouncer would have cut silently.
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_pool_timeout_s: int = 10
    db_pool_recycle_s: int = 1800

    # Identity
    oidc_issuer: str = "http://localhost:8080/realms/choregos"
    oidc_client_id: str = "choregos"
    oidc_client_secret: str = ""
    oidc_scopes: str = "openid profile email groups"
    #: The platform's token-exchange client (RFC 8693, ADR 0135): the client that swaps a person's token
    #: for a source's audience. Empty: the console's own client and token endpoint.
    token_exchange_client_id: str = ""
    token_exchange_client_secret: str = ""
    token_exchange_endpoint: str = ""
    #: The audiences a person's token may be exchanged for. Empty: any audience a connector declares (the
    #: provider's own exchange permissions then decide); set it in production (security review S63-07).
    token_exchange_audiences: list[str] = Field(default_factory=list)
    #: RFC 7009 revocation endpoint of the provider, to kill a delegation's offline token (S63-14). Empty:
    #: the fake's under CHOREGOS_FAKES, none otherwise (the sealed token is then only deleted here).
    token_revocation_endpoint: str = ""
    #: For an IdP that publishes no discovery document; set all three or none (ADR 0100).
    oidc_authorization_endpoint: str = ""
    oidc_token_endpoint: str = ""
    oidc_userinfo_endpoint: str = ""
    #: An existing account is linked by e-mail only when the provider does not say
    #: `email_verified: false` (Entra omits the claim, so only an explicit false refuses).
    oidc_require_email_verified: bool = True
    #: Temporary switch for an identity provider MIGRATION (ADR 0100): an account bound to another
    #: subject is re-linked when the provider asserts `email_verified: true`. Off: it is refused.
    oidc_relink_verified_email: bool = False
    #: The MCP door also accepts OAuth tokens issued by the IdP (ADR 0030, RFC 9728): claude.ai and
    #: hosted clients cannot present a `chg_` token. Off by default.
    mcp_oauth_enabled: bool = False
    #: How long an `ask` through the MCP door waits for the assistant's turn before answering `working`
    #: (S63-11): the turn goes on in the background and `get_conversation` returns it when done.
    mcp_assistant_wait_s: float = 25.0
    #: The issuer of the door's tokens; empty: that of the console's OIDC sign-in.
    mcp_oauth_issuer: str = ""
    #: The MANDATORY audience of a door token: on a shared realm, a token obtained by another
    #: application must not open it.
    mcp_oauth_audience: str = "choregos-mcp"
    #: The OAuth clients that the IdP registered for the door, by client of the Integrations page
    #: (`claude-code`, `claude-ai`...), as JSON: `{"claude-code": {"client_id": "...",
    #: "callback_port": 33418}}`. The page derives the exact command from them; without them, it
    #: offers only tokens.
    mcp_oauth_clients: dict[str, OAuthClient] = Field(default_factory=dict)
    session_secret: str = DEFAULT_SESSION_SECRET
    #: A verification-only key during a rotation (`CHOREGOS_SESSION_SECRET_PREVIOUS`): a value signed
    #: with it still reads, and nothing is signed with it.
    session_secret_previous: str = ""
    session_cookie: str = "choregos_session"
    session_max_age_s: int = 8 * 3600
    #: Development sign-in (`/auth/login?as=<email>`): OFF by default. It used to be on by default
    #: and never set by the chart: a production installation accepted `?code=dev:admin@x` and
    #: returned ORG_ADMIN on all organisations (inventory of 2026-09-24). The validator below
    #: refuses it in staging and prod whatever it is told.
    dev_login_enabled: bool = False
    #: The e-mails that, in development sign-in, receive ORG_ADMIN. The others are `developers`.
    #: Before: any e-mail starting with `admin`, an escalation by naming convention.
    dev_admin_emails: str = ""
    #: Bootstrap: the organisation created at the first start if it does not exist, and the e-mails
    #: that receive ORG_ADMIN in it. Without it, a new installation had NO way to create an
    #: organisation (only the seed scripts did), hence no project, hence nothing (inventory of
    #: 2026-09-24). Idempotent: replayed at every start, it does not touch what exists.
    bootstrap_org: str = ""
    bootstrap_org_name: str = ""
    bootstrap_admins: str = ""
    #: Organisation to which the NON-prefixed OIDC groups (`developers`, `org-admins`...) attach.
    #: Empty: those groups are ignored, only the `choregos:<org>:<group>` groups give a role.
    #: Before: any group gave the role on ALL the organisations of the instance.
    oidc_default_org: str = ""

    # Run JWT (ES256): PEM keys; generated on the fly in dev
    run_token_private_key: str = ""
    run_token_public_key: str = ""
    run_token_issuer: str = "choregos-api"
    run_token_audience: str = "internal"

    #: Hosts no run of this deployment may reach, whatever an organisation or a project says (ADR 0070):
    #: a comma-separated list of host patterns (`CHOREGOS_NETWORK_INSTANCE_DENY`).
    network_instance_deny: str = ""
    #: Where an operator can reach this deployment, written into the User-Agent of what the platform
    #: fetches (`CHOREGOS_USER_AGENT_CONTACT`); an organisation's own contact wins (ADR 0070).
    user_agent_contact: str = ""

    # Services
    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "orchestrator"
    #: The tracker catch-up interval, as the orchestrator reads it (`CHOREGOS_RECONCILE_INTERVAL_SECONDS`):
    #: the API starts a project's loop when the project becomes active; 0 disables the catch-up.
    reconcile_interval_seconds: int = 60
    gateway_url: str = "http://localhost:4000"
    #: The gateway adapter the deployment uses (`litellm`, `openai-compatible`, `direct`...).
    gateway_type: str = "litellm"
    gateway_master_key: str = ""
    #: The model a voice session has the gateway transcribe the caller's speech with (ADR 0080 §10):
    #: the transcript is what is kept of it, never the audio. Empty: the caller's words are not kept.
    realtime_transcription_model: str = "gpt-4o-mini-transcribe"
    # What THIS deployment can run (#245): a new project inherits it when its request says nothing.
    # Without them, it starts on `claude-code` and no model profile; on the dev tenant, which serves
    # opencode behind the gateway, the first agent of the HR template never reached the model. Lists
    # and tables as JSON: `CHOREGOS_ALLOWED_AGENT_BACKENDS='["opencode"]'`,
    # `CHOREGOS_DEFAULT_MODEL_PROFILES='{"standard": "platform/standard"}'`.
    default_agent_backend: str | None = None
    # How long the broker waits for the outcome of a write called by a run, played as a governed
    # action (#241), before returning 202 and the action's identifier. The field keeps its French
    # name: it is an environment variable, `CHOREGOS_COURTIER_ATTENTE_ECRITURE_S`.
    courtier_attente_ecriture_s: float = 30.0  # lang: fr-ok environment variable name
    #: The most a broker tool result may weigh, as JSON bytes, when it reaches the agent (#755): a
    #: larger one is cut to its text with a marker that says so. A third-party server does not decide
    #: how much of the agent's context, and of its token budget, it fills. Its data is counted once: a
    #: text block that serializes its `structuredContent` is not counted again (#1374). The default is
    #: the bound the run's tool server shows the model (`choregos_core.mcp.RESULT_MAX_BYTES`).
    broker_result_max_bytes: int = Field(default=RESULT_MAX_BYTES, ge=1024)
    #: The most an operation or a catalogue tool may raise its own cap to (S40-17): an organisation
    #: that needs one large answer opens it for that operation, never for the whole platform. The
    #: default is what the prompt loop lets through of one tool answer (`RESULT_CEILING_BYTES`).
    broker_result_ceiling_bytes: int = Field(default=RESULT_CEILING_BYTES, ge=1024)
    allowed_agent_backends: list[str] = Field(default_factory=list)
    #: The IdP groups whose members hold `assistant:audit` on every assistant of the platform: the
    #: privacy officers (S63-15). Owners hold it on their own assistant; no organisation role carries it.
    assistant_privacy_officer_groups: list[str] = Field(default_factory=list)
    #: The hosts the API pod may reach, as the deployment's network policy lets it (`*.` wildcards
    #: allowed). Empty means the deployment does not say: the MCP catalogue then answers `unknown`
    #: instead of `undeclared` (ADR 0065). It restricts nothing; the network policy does.
    #: Deprecated (S39-22): read as `CHOREGOS_EGRESS_CEILING` `{"api": …}` when that names no API family.
    api_egress_hosts: list[str] = Field(default_factory=list)
    #: The operator's own MCP catalogue entries, merged after the shipped catalogue (ADR 0065).
    mcp_catalogue_extra: str | None = None
    #: The images an operator names for job steps (ADR 0056) and for agents written in code (ADR 0063),
    #: as JSON: name -> reference. The orchestrator reads the same variables; the API validates a
    #: version against the names, and lists them (names only) for the console.
    job_images: dict[str, str] = Field(default_factory=dict)
    agent_images: dict[str, str] = Field(default_factory=dict)
    #: A reference without `@sha256:` is refused at start-up (the chart sets it; a local setup may not).
    require_image_digest: bool = False
    #: Where a hosted agent's image may come from (ADR 0080 §8), as registry or repository prefixes
    #: matched by path segment (`ghcr.io/acme` covers `ghcr.io/acme/triager`). Empty: no image is
    #: accepted, only zip sources the platform builds.
    hosted_registries: list[str] = Field(default_factory=list)
    #: Whether a hosted image must carry a trusted signature: `off`, `warn` (log and publish) or
    #: `enforce` (refuse at publication).
    hosted_cosign: Literal["off", "warn", "enforce"] = "off"
    #: Who may sign a hosted image (S41-14): a public key cosign reads (a path, the chart mounts the
    #: operator's Secret at `/etc/choregos/cosign/cosign.pub`, or a KMS URI), or keyless, the
    #: certificate identity and its OIDC issuer as regular expressions matched whole. Neither, or
    #: both: nothing verifies, and `enforce` refuses every image.
    hosted_cosign_key: str = ""
    hosted_cosign_identity: str = ""
    hosted_cosign_issuer: str = ""
    #: Whether cosign checks the transparency log (Rekor). `false` only for a key whose signatures
    #: were never uploaded, an air-gapped registry.
    hosted_cosign_tlog: bool = True
    #: The plugin sources, default plugins and offline directory (ADR 0093, S43-03): a path to a YAML
    #: file (`sources`, `defaultPlugins`, `offline`) the chart writes. Empty: no source but the offline one.
    plugins_config: str = ""
    #: The offline plugin directory baked into the image; wins over the file's `offline`.
    plugins_offline: str = ""
    #: Plugin servers (ADR 0096): `kubernetes` runs them in `plugin_servers_namespace` through the API's
    #: Role there; `off` refuses a plugin that brings one (`CHOREGOS_FAKES` runs them in memory).
    plugin_servers: Literal["off", "kubernetes"] = "off"
    plugin_servers_namespace: str = "choregos-plugins"
    #: Where a server's image may come from, as registry or repository prefixes matched by path
    #: segment (the rule of `hosted_registries`). Empty: no server runs. Signatures follow
    #: `hosted_cosign` and its signer (ADR 0080 §8).
    plugin_servers_registries: list[str] = Field(default_factory=list)
    #: The RuntimeClass of the servers' pods (gVisor); empty: the cluster's runtime.
    plugin_servers_runtime_class: str = ""
    #: How egress is narrowed: `networkpolicy` (public addresses on the configured ports) or `cilium`
    #: (a CiliumNetworkPolicy naming the exact hosts).
    plugin_servers_egress: Literal["networkpolicy", "cilium"] = "networkpolicy"
    #: The namespace of the API's pods, the only ones a server admits; empty: any namespace.
    plugin_servers_api_namespace: str = ""
    plugin_servers_cpu: str = "500m"
    plugin_servers_memory: str = "512Mi"
    #: The base image of each zip runtime, `ref@sha256:…`, over the shipped ones (ADR 0080 §9):
    #: `{"python_3_13": "registry.internal/python:3.13-slim@sha256:…"}`.
    hosted_base_images: dict[str, str] = Field(default_factory=dict)
    #: What builds a zip version's image (ADR 0080 §9): a rootless `buildkitd`, or the Docker of a
    #: laptop (`docker compose`, `make dev-up`).
    hosted_builder: Literal["buildkit", "local_docker"] = "buildkit"
    #: The repository built images are pushed to, `registry/path` (`global.images.buildRegistry`).
    #: Empty: a zip version is published, and its build fails saying no repository is configured.
    hosted_build_repository: str = ""
    #: How long a hosted session's pod lives without a response before it is stopped (S41-11);
    #: the session's own expiry bounds it too.
    hosted_session_idle_s: int = Field(default=600, ge=30)
    #: The Kubernetes version hosted agents run on, as the API server writes it (`v1.29.3-eks-abc`);
    #: the chart sets it from the cluster it is installed on. Below 1.29 a hosted version is refused
    #: at publication (S41-16). Empty (`docker compose`, `make dev-up`): no cluster, nothing checked.
    cluster_version: str = ""
    #: The `buildkitd` builds go through, as `buildctl --addr` takes it: the one the chart deploys
    #: (`global.buildkitd`, S41-13) unless `global.images.buildkitAddress` names another.
    buildkit_address: str = "tcp://choregos-buildkitd:1234"
    #: A directory holding `ca.crt`, `tls.crt` and `tls.key` for mutual TLS with `buildkitd`;
    #: empty speaks plain gRPC, which the chart's NetworkPolicy limits to the orchestrator's pods.
    buildkit_tls_dir: str = ""
    default_model_profiles: dict[str, str] = Field(default_factory=dict)
    #: The organisation egress allowlist (S39-22, ADR 0066). `CHOREGOS_EGRESS_MODE` is `enforce`,
    #: `warn` (log `egress.would_refuse` and pass) or `off`; empty means `enforce`, and `warn` under
    #: `CHOREGOS_FAKES=1`.
    egress_mode: str = ""
    #: What the cluster's network lets through, per pod family (`api`, `workers`, `runners`), as host
    #: patterns: `CHOREGOS_EGRESS_CEILING='{"api": ["api.github.com"]}'`. An absent or empty family
    #: is unknown: the ceiling is not checked and the organisation's list alone governs.
    egress_ceiling: dict[str, list[str]] = Field(default_factory=dict)
    #: How the console renders "what the platform must still open": `format`, `template` (mustache:
    #: `{{hosts_csv}}`, `{{#hosts}}{{.}}{{/hosts}}`, `{{family}}`, `{{ports}}`, `{{reason}}`,
    #: `{{origin}}`, `{{requested_by}}`, `{{entry}}`, `{{selector}}`), `issueUrl`, `owner`, `entries`.
    egress_platform_change: dict[str, Any] | None = None
    #: Hosts an operator lists so that an organisation's MCP gateway may be plain http or a private
    #: name (ADR 0105): `CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS`, a JSON list of exact host names. Any
    #: other private or plain-http gateway address is refused when the setting is written.
    mcp_gateway_internal_hosts: list[str] = Field(default_factory=list)
    memory_url: str = "http://localhost:8432"
    object_store_url: str = "s3://choregos"
    #: The S3 endpoint (RustFS in development: `http://localhost:9000`); empty means AWS.
    object_store_endpoint: str = ""
    object_store_region: str = "us-east-1"
    object_store_access_key: str = ""
    object_store_secret_key: str = ""
    #: The largest file artifact the internal API accepts from a runner, in bytes (ADR 0049).
    artifact_max_bytes: int = 200 * 1024 * 1024

    # Skill scanner (ADR 0095): SkillSpector, offline, beside the gate's own scan (ADR 0050).
    #: `skillspector` runs it on every version a person publishes; `off` (the default outside the
    #: image, where SkillSpector is not installed) keeps the gate's own scan alone.
    skill_scanner: Literal["off", "skillspector"] = "off"
    #: The interpreter of SkillSpector's own environment, built into the API image.
    skillspector_python: str = "/opt/skillspector/bin/python"
    #: A scan runs in a child process, killed past this delay: the version is then held for review.
    skill_scan_timeout_s: float = 120.0
    #: Where OSV's PyPI and npm archives are read from to build the local mirror; empty: no download
    #: (an air-gapped deployment copies a mirror file in, or the dependency check stays incomplete).
    osv_mirror_url: str = "https://osv-vulnerabilities.storage.googleapis.com"
    #: The mirror file, one per API replica, on its `/tmp` volume (about 1 GB with npm).
    osv_mirror_path: str = "/tmp/choregos-osv/osv.sqlite"  # noqa: S108 - the replica's own /tmp volume
    #: How often the mirror is rebuilt, and past which age a scan no longer uses it.
    osv_refresh_hours: float = 24.0
    osv_max_age_hours: float = 72.0

    # Knowledge bases (ADR 0051)
    #: The largest file an upload accepts; a bigger one is refused before it is read in full.
    knowledge_max_upload_mib: int = 20
    #: How many documents one knowledge base holds.
    knowledge_max_documents: int = 500
    #: The largest markdown a conversion may produce; past it the document fails, it is not cut.
    knowledge_max_markdown_mib: int = 8
    #: The most rows a workbook's sheet gives once converted; the rest is counted, not shown (S42-09c).
    knowledge_xlsx_rows_per_sheet: int = 2000
    #: A conversion runs in a child process, killed past this delay.
    knowledge_conversion_timeout_s: float = 120.0
    #: How long a connector's connection test may take, when it runs on save (S40-05).
    connector_test_timeout_s: float = 10.0
    #: How many conversions run at once in one API replica. One by default: the API pod has one CPU
    #: (S39-26), a second converter would only share it.
    knowledge_conversion_concurrency: int = 1
    #: Distillation in passes (S42-10c, ADR 0082): the corpus is read in parts of at most this many
    #: characters (about 75 k tokens), one model call each.
    knowledge_distill_part_chars: int = 300_000
    #: The most parts one distillation reads; a bigger base is refused, naming its biggest documents.
    knowledge_distill_max_parts: int = 16
    #: The most one distillation may spend, in US dollars, when the model is priced.
    knowledge_distill_budget_usd: float = 10.0
    #: The output a call is assumed to produce, to estimate a cost before spending it.
    knowledge_distill_output_tokens_per_call: int = 6_000
    #: The gateway model that embeds knowledge chunks, unless `default_model_profiles["embed"]` says.
    knowledge_embedding_model: str = "platform/embed"
    #: A directory of `<id>.site.md` notes and `<new-id>.md` procedures a deployment adds to the
    #: setup procedures that ship with the platform (ADR 0067). Empty: none.
    procedures_extra: str = ""
    public_url: str = "http://localhost:3000"
    api_url: str = "http://localhost:8000"

    # Webhooks
    github_webhook_secret: str = ""
    github_app_id: str = ""
    github_app_private_key: str = ""
    #: The App's OAuth client (optional): only needed to prove, on an instance with several
    #: organisations, that the person who installs the App manages that installation (ADR 0073).
    github_app_client_id: str = ""
    github_app_client_secret: str = ""
    #: Shared secret of the unsigned webhooks (ArgoCD, Alertmanager, Jira, GitLab). EMPTY by
    #: default: `verify_shared_secret` then refuses everything, and that is intended: the former
    #: default value was published in this repository, and the chart did not inject it.
    generic_webhook_secret: str = ""

    # Miscellaneous
    cors_origins: str = "http://localhost:3000"
    #: Requests per minute and per address on the routes without a principal (`/auth/*`,
    #: `/webhooks/*`), per replica. 0 disables. See `rate_limit.py`.
    rate_limit_per_minute: int = 600
    #: The networks of the proxies in front of the API, as a JSON list
    #: (`CHOREGOS_TRUSTED_PROXY_CIDRS='["10.0.0.0/8"]'`): the rate limit keys on the address the
    #: nearest trusted proxy saw, the right-most `X-Forwarded-For` entry outside them. Empty: the TCP
    #: peer is the client and `X-Forwarded-For` is ignored.
    trusted_proxy_cidrs: list[str] = Field(default_factory=list)
    #: Used only without `trusted_proxy_cidrs`: the number of proxies in front of the API that each
    #: append one `X-Forwarded-For` entry. 0: the TCP peer is the client.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=10)
    #: Period of the recomputation of the Prometheus metrics from the database (see `metrics.py`).
    metrics_refresh_seconds: float = 15.0
    #: Seconds between two passes of the approved-actions relay, in every replica (ADR 0085).
    action_relay_interval_s: float = 30.0
    #: Seconds between two passes of the assistants' retention purge (S63-16): daily.
    assistant_retention_interval_s: float = 86400.0
    #: Tempo's query address, read by the nightly ledger-vs-trace job (`trace_drift.py`, ADR 0018
    #: point 3). Empty: the job refuses to run.
    tempo_url: str = ""
    #: How far a trace's token count may stray from the ledger's before it is a drift: a share of
    #: the ledger's figure, never less than one token.
    trace_drift_tolerance: float = 0.01
    log_level: str = "INFO"
    log_json: bool = True
    fx_usd_eur: float = 0.92

    @property
    def cookies_secure(self) -> bool:
        """Whether the session and OIDC cookies carry `Secure`.

        Always in staging/prod; and in any environment whose public or API URL is https, because
        a deployment behind a TLS-terminating ingress is served over HTTPS whatever `env` says
        (a dev tenant on https must not send its session cookie in clear on a stray http hop).
        """
        https = any(u.strip().lower().startswith("https://") for u in (self.public_url, self.api_url))
        return self.env in {"staging", "prod"} or https

    @property
    def deployed(self) -> bool:
        """Whether this instance is reachable as a deployment.

        True in staging/prod, and whatever `env` says when it is served over https (the config and
        the chart both default `env` to dev). S47-02 and S47-13 reuse it.
        """
        return self.cookies_secure

    @model_validator(mode="after")
    def _no_back_door_in_production(self) -> Settings:
        if self.dev_login_enabled and self.env in {"staging", "prod"}:
            raise ValueError(
                "CHOREGOS_DEV_LOGIN_ENABLED=true in staging/prod: the development sign-in "
                "opens the API to anyone who reaches /auth/callback. Refused."
            )
        return self

    @model_validator(mode="after")
    def _oidc_endpoints_all_or_none(self) -> Settings:
        explicit = [self.oidc_authorization_endpoint, self.oidc_token_endpoint, self.oidc_userinfo_endpoint]
        if any(explicit) and not all(explicit):
            raise ValueError(
                "set CHOREGOS_OIDC_AUTHORIZATION_ENDPOINT, CHOREGOS_OIDC_TOKEN_ENDPOINT and "
                "CHOREGOS_OIDC_USERINFO_ENDPOINT together, or none"
            )
        return self

    @model_validator(mode="after")
    def _trusted_proxies_are_networks(self) -> Settings:
        """A typo in a trusted network would silently key every client on the proxy: refused.

        Safe in every process: the worker processes do not set these variables.
        """
        for entry in self.trusted_proxy_cidrs:
            try:
                ipaddress.ip_network(entry, strict=False)
            except ValueError:
                raise ValueError(f"CHOREGOS_TRUSTED_PROXY_CIDRS: `{entry}` is not a network") from None
        if self.trusted_proxy_cidrs and self.trusted_proxy_hops > 0:
            raise ValueError("set CHOREGOS_TRUSTED_PROXY_CIDRS or CHOREGOS_TRUSTED_PROXY_HOPS, not both")
        return self

    @model_validator(mode="after")
    def _images_named_by_the_operator(self) -> Settings:
        check_image_map("CHOREGOS_JOB_IMAGES", self.job_images, require_digest=self.require_image_digest)
        check_image_map("CHOREGOS_AGENT_IMAGES", self.agent_images, require_digest=self.require_image_digest)
        return self

    @model_validator(mode="after")
    def _oauth_clients_the_page_knows(self) -> Settings:
        """A misspelt key (`claude_code`) would show nothing, silently: we refuse."""
        unknown = sorted(set(self.mcp_oauth_clients) - OAUTH_CLIENTS)
        if unknown:
            raise ValueError(
                f"CHOREGOS_MCP_OAUTH_CLIENTS: unknown clients {unknown}; the Integrations page "
                f"knows {sorted(OAUTH_CLIENTS)}"
            )
        return self

    @model_validator(mode="after")
    def _no_unresolved_template(self) -> Settings:
        """A setting whose value is `<no value>` is not a value: it is a template that failed.

        On 2026-09-26, on the dev tenant, `GATEWAY_MASTER_KEY` and `PLATFORM_LLM_KEY` both held the
        string `<no value>`: ten characters, looking like a value. The LiteLLM pod was `1/1
        Running`, the tenant looked healthy, and the gateway answered 401 at the first model call:
        "LiteLLM Virtual Key expected. Received=<no value>". Nothing, anywhere, said that the key
        had never been resolved.

        It is a failure mode of every secret rendered by a Go template, Helm as well as the
        Infisical operator, and it touches the session secret as much as the token keys. We refuse
        to start, in ALL environments: nobody writes `<no value>` on purpose.
        """
        faulty = sorted(
            name
            for name, value in self.__dict__.items()
            if isinstance(value, str) and value.strip() in UNRESOLVED_TEMPLATE
        )
        if faulty:
            # The message stays French: docs/deployment.md tells the operator to look for it.
            raise ValueError(
                f"réglages non résolus : {faulty}. "  # lang: fr-ok
                "Leur valeur est littéralement « <no value> », "  # lang: fr-ok
                "ce qu'un gabarit Go écrit quand la variable n'existe pas — "  # lang: fr-ok
                "la clé n'a jamais été posée. "  # lang: fr-ok
                "Vérifier la source du secret (Infisical, Helm) avant de redémarrer."  # lang: fr-ok
            )
        return self

    @property
    def bootstrap_admin_emails(self) -> list[str]:
        return [e.strip().lower() for e in self.bootstrap_admins.split(",") if e.strip()]

    @property
    def dev_admins(self) -> set[str]:
        return {e.strip().lower() for e in self.dev_admin_emails.split(",") if e.strip()}

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
