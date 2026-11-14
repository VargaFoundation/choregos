{{/* Short name and labels common to all the platform's objects. */}}
{{- define "choregos.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "choregos.labels" -}}
app.kubernetes.io/name: {{ include "choregos.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: choregos
{{- end -}}

{{/*
Image reference: by digest as soon as `global.imageDigest` is provided (Kyverno requirement).

A component can come from ANOTHER registry (`image.registry`): the enterprise edition serves its
API and orchestrator images from a private project, the rest comes from the core (ADR 0032,
S17-03). An image from another registry is another image: the global digest does not designate it,
only its `image.digest` or its tag count.
*/}}
{{- define "choregos.image" -}}
{{- $global := .global -}}
{{- $ailleurs := and .image.registry (ne .image.registry $global.imageRegistry) -}}
{{- $repo := printf "%s/%s" (default $global.imageRegistry .image.registry) .image.repository -}}
{{- if .image.digest -}}
{{ $repo }}@{{ .image.digest }}
{{- else if and $global.imageDigest (not $ailleurs) -}}
{{ $repo }}@{{ $global.imageDigest }}
{{- else -}}
{{ $repo }}:{{ default $global.imageTag .image.tag }}
{{- end -}}
{{- end -}}

{{/*
The executor that runs agent stages (ADR 0072), one answer for every pod. The API needs it as
much as the workers: it showed "tekton" on the project settings page while the workers ran
`k8s_job`, because only the workers received the variable. `global.runner.executor` is the
setting; the orchestrator sub-chart's own `runner.executor` may only repeat it (a different value
there would bring back the two answers, so the render refuses it).
*/}}
{{- define "choregos.executorKind" -}}
{{- $global := (.Values.global.runner | default dict).executor | default "agent_sandbox" -}}
{{- $local := "" -}}
{{- with .Values.runner }}{{- $local = .executor | default "" -}}{{- end -}}
{{- if and $local (ne $local $global) -}}
{{- fail (printf "runner.executor=%s on the orchestrator differs from global.runner.executor=%s. Set the executor once, in global.runner.executor: the API and the workers must agree on it." $local $global) -}}
{{- end -}}
{{- $global -}}
{{- end -}}

{{/*
The RuntimeClass of the warm pool's template (gVisor, Kata). `global.runner.runtimeClass` is the
setting that the API also reads (S38-13d): when it is SET (even to "", the cluster's default), the API
and every worker receive it and `/readiness` compares it with what a project's policy asks. When it
is NEVER set, the orchestrator sub-chart's own `runner.runtimeClass` still applies to the pool as
before, but the API is told nothing and treats the pool's class as unknown (no judgment, no false
alarm). Both set and different: refused, that is a contradiction.
*/}}
{{- define "choregos.runtimeClass" -}}
{{- $runner := .Values.global.runner | default dict -}}
{{- $local := "" -}}
{{- with .Values.runner }}{{- $local = .runtimeClass | default "" -}}{{- end -}}
{{- if hasKey $runner "runtimeClass" -}}
{{- $global := $runner.runtimeClass | default "" -}}
{{- if and $local (ne $local $global) -}}
{{- fail (printf "runner.runtimeClass=%s on the orchestrator differs from global.runner.runtimeClass=%q: set the RuntimeClass once, in global.runner.runtimeClass." $local $global) -}}
{{- end -}}
{{- $global -}}
{{- else -}}
{{- $local -}}
{{- end -}}
{{- end -}}

{{/*
The namespace pattern the runs go to. Empty (the default) follows the executor: the release
namespace for `agent_sandbox`, whose SandboxTemplate lives there, `proj-{slug}-runners` otherwise.
An explicit value always wins.
*/}}
{{- define "choregos.runnerNamespacePattern" -}}
{{- if .Values.runner.namespacePattern -}}
{{- .Values.runner.namespacePattern -}}
{{- else if eq (include "choregos.executorKind" .) "agent_sandbox" -}}
{{- .Release.Namespace -}}
{{- else -}}
proj-{slug}-runners
{{- end -}}
{{- end -}}

{{/* Common environment variables: 12-factor configuration, secrets by reference. */}}
{{- define "choregos.commonEnv" -}}
{{- $poses := list "CHOREGOS_ENV" "CHOREGOS_EDITION" "CHOREGOS_LOG_LEVEL" "CHOREGOS_FAKES" "CHOREGOS_EGRESS_MODE" "CHOREGOS_EGRESS_CEILING" "CHOREGOS_EGRESS_PLATFORM_CHANGE" "CHOREGOS_TEMPORAL_ADDRESS" "CHOREGOS_TEMPORAL_NAMESPACE" "CHOREGOS_GATEWAY_URL" "CHOREGOS_MEMORY_URL" "CHOREGOS_MEMORY_TOKEN" "CHOREGOS_OBJECT_STORE_URL" "CHOREGOS_OBJECT_STORE_ENDPOINT" "CHOREGOS_OBJECT_STORE_REGION" "CHOREGOS_OBJECT_STORE_ACCESS_KEY" "CHOREGOS_OBJECT_STORE_SECRET_KEY" "CHOREGOS_PUBLIC_URL" "CHOREGOS_API_URL" "CHOREGOS_DB_POOL_SIZE" "CHOREGOS_DB_MAX_OVERFLOW" "CHOREGOS_DB_POOL_TIMEOUT_S" "CHOREGOS_DATABASE_PASSWORD" "CHOREGOS_DATABASE_URL" "CHOREGOS_SECRET_ENV_PATTERN" "CHOREGOS_EXECUTOR_KIND" "CHOREGOS_RUNNER_RUNTIME_CLASS" "CHOREGOS_EXECUTORS_ALLOWED" }}
{{- /* Before S40-20b the endpoint went through `extraEnv`: still accepted, unless the chart sets it. */}}
{{- if (.Values.global.tracing | default dict).otlpEndpoint }}
{{- $poses = append $poses "OTEL_EXPORTER_OTLP_ENDPOINT" }}
{{- end }}
{{- $gw := .Values.global.mcpGateway | default dict }}
{{- if $gw.internalHosts }}
{{- $poses = append $poses "CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS" }}
{{- end }}
{{- /* Before S65-16 the outposts' settings went through `extraEnv` too: still accepted while
       `global.outposts.enabled` is false, refused once the chart sets them. */}}
{{- if include "choregos.outpostsEnabled" . }}
{{- $poses = concat $poses (splitList " " (include "choregos.outpostsOwnedEnv" "common")) }}
{{- end }}
{{- range (.Values.global.extraEnv | default list) }}
{{- if has .name $poses }}
{{- fail (printf "global.extraEnv: %s is already set by the chart; two values for the same name, Kubernetes would keep the last one without saying anything" .name) }}
{{- end }}
{{- /* S38-18: `value` or `valueFrom`, exactly one — an entry with neither rendered `value: ""`, a
     key that reached every pod empty without anything saying so. */}}
{{- if eq (hasKey . "value") (hasKey . "valueFrom") }}
{{- fail (printf "global.extraEnv: %s needs exactly one of value or valueFrom" .name) }}
{{- end }}
{{- end }}
- name: CHOREGOS_ENV
  value: {{ .Values.global.environment | quote }}
- name: CHOREGOS_EDITION
  value: {{ .Values.global.edition | default "community" | quote }}
- name: CHOREGOS_LOG_LEVEL
  value: {{ .Values.global.logLevel | quote }}
- name: CHOREGOS_FAKES
  value: {{ .Values.global.fakes | quote }}
{{- $egress := .Values.global.egress | default dict }}
- name: CHOREGOS_EGRESS_MODE
  value: {{ $egress.mode | default "enforce" | quote }}
{{- /* ADR 0105: the gateway hosts join the families the organisation declared, never `runners`. */}}
{{- $ceiling := deepCopy ($egress.ceiling | default dict) }}
{{- range $family := list "api" "workers" }}
{{- if hasKey $ceiling $family }}
{{- $_ := set $ceiling $family (uniq (concat (get $ceiling $family) ($gw.hosts | default list))) }}
{{- end }}
{{- end }}
{{- if $ceiling }}
- name: CHOREGOS_EGRESS_CEILING
  value: {{ toJson $ceiling | quote }}
{{- end }}
{{- if $gw.internalHosts }}
- name: CHOREGOS_MCP_GATEWAY_INTERNAL_HOSTS
  value: {{ toJson $gw.internalHosts | quote }}
{{- end }}
{{- with $egress.platformChange }}
- name: CHOREGOS_EGRESS_PLATFORM_CHANGE
  value: {{ toJson . | quote }}
{{- end }}
- name: CHOREGOS_TEMPORAL_ADDRESS
  value: {{ include "choregos.temporalAddress" . | quote }}
- name: CHOREGOS_TEMPORAL_NAMESPACE
  value: {{ .Values.global.temporal.namespace | quote }}
- name: CHOREGOS_EXECUTOR_KIND
  value: {{ include "choregos.executorKind" . | quote }}
{{- if hasKey (.Values.global.runner | default dict) "runtimeClass" }}
# The warm pool's RuntimeClass, for the API and the workers alike (empty: the cluster's default).
# Absent when `global.runner.runtimeClass` is never set: the API then knows nothing.
- name: CHOREGOS_RUNNER_RUNTIME_CLASS
  value: {{ include "choregos.runtimeClass" . | quote }}
{{- end }}
- name: CHOREGOS_EXECUTORS_ALLOWED
  value: {{ (.Values.global.runner | default dict).allowedExecutors | default list | join "," | quote }}
{{- with (.Values.global.tracing | default dict).otlpEndpoint }}
# ADR 0018: the collector the traces go to; absent, `configure_tracing` leaves tracing off.
- name: OTEL_EXPORTER_OTLP_ENDPOINT
  value: {{ . | quote }}
{{- end }}
- name: CHOREGOS_GATEWAY_TYPE
  value: {{ .Values.global.gateway.type | default "litellm" | quote }}
- name: CHOREGOS_GATEWAY_URL
  value: {{ include "choregos.gatewayUrl" . | quote }}
- name: CHOREGOS_MEMORY_URL
  value: {{ include "choregos.memoryUrl" . | quote }}
{{- with (include "choregos.memoryTokenSecret" .) }}
- name: CHOREGOS_MEMORY_TOKEN
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: token
{{- end }}
- name: CHOREGOS_OBJECT_STORE_URL
  value: {{ .Values.global.objectStore.url | quote }}
- name: CHOREGOS_OBJECT_STORE_ENDPOINT
  value: {{ .Values.global.objectStore.endpoint | default "" | quote }}
- name: CHOREGOS_OBJECT_STORE_REGION
  value: {{ .Values.global.objectStore.region | default "us-east-1" | quote }}
{{- with .Values.global.objectStore.secretRef }}
- name: CHOREGOS_OBJECT_STORE_ACCESS_KEY
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: access-key
- name: CHOREGOS_OBJECT_STORE_SECRET_KEY
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: secret-key
{{- end }}
- name: CHOREGOS_PUBLIC_URL
  value: {{ .Values.global.publicUrl | default (printf "https://app.%s" .Values.global.domain) | quote }}
- name: CHOREGOS_API_URL
  value: {{ .Values.global.apiUrl | default (printf "https://api.%s" .Values.global.domain) | quote }}
{{- /* The connection pool, BOUNDED: `pool_size` alone let SQLAlchemy open ten
       overflow connections per process with no wait-time limit (status review
       of 2026-09-24). Each API replica and each worker holds at most size+maxOverflow
       connections; multiply by the replicas to size `max_connections`. */}}
- name: CHOREGOS_DB_POOL_SIZE
  value: {{ .Values.global.database.pool.size | quote }}
- name: CHOREGOS_DB_MAX_OVERFLOW
  value: {{ .Values.global.database.pool.maxOverflow | quote }}
- name: CHOREGOS_DB_POOL_TIMEOUT_S
  value: {{ .Values.global.database.pool.timeoutSeconds | quote }}
{{- include "choregos.databaseEnv" . }}
{{- with (.Values.global.secrets | default dict).envPattern }}
- name: CHOREGOS_SECRET_ENV_PATTERN
  value: {{ . | quote }}
{{- end }}
{{- if include "choregos.outpostsEnabled" . }}
{{- /* Outposts (ADR 0147 §12): the switch and the quotas of calls in flight, for whoever places a
       call: the API for a read, the workers for a governed write (ADR 0149 §4-5). */}}
{{ include "choregos.outpostsEnv" . }}
{{- end }}
{{- range (.Values.global.extraEnv | default list) }}
- name: {{ .name }}
{{- if hasKey . "valueFrom" }}
  valueFrom:
    {{- toYaml .valueFrom | nindent 4 }}
{{- else }}
  value: {{ .value | quote }}
{{- end }}
{{- end }}
{{- end -}}

{{/*
Where the database is, as a process reads it (`CHOREGOS_DATABASE_URL`). Its own template: the
outposts' door served alone (`templates/outposts-door.yaml`) takes it without the rest of
`choregos.commonEnv`.
*/}}
{{- define "choregos.databaseEnv" -}}
{{- with (include "choregos.databasePasswordSecret" .) }}
{{- /* The password comes from a secret laid down by an operator (Zalando: `password` key),
       the URL is composed here — Kubernetes expands `$(VAR)` of a variable declared earlier. */}}
- name: CHOREGOS_DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: password
- name: CHOREGOS_DATABASE_URL
  {{- /* Embedded: the application role without SUPERUSER created at initdb. External: the role
         the operator created (Zalando, CNPG), already without SUPERUSER — `choregos_app` does not exist there. */}}
  value: {{ printf "postgresql+asyncpg://%s:$(CHOREGOS_DATABASE_PASSWORD)@%s:%v/%s" (ternary ($.Values.global.database.appUser | default $.Values.global.database.user) $.Values.global.database.user $.Values.global.database.embedded) (include "choregos.databaseHost" $) $.Values.global.database.port $.Values.global.database.name | quote }}
{{- else }}
- name: CHOREGOS_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.global.database.secretRef }}
      key: url
{{- end }}
{{- end -}}

{{/*
Outposts (ADRs 0147-0149). `global.outposts` is read by the API's subchart, by the workers' and by
this chart's own templates: these helpers give them one answer.

`choregos.outpostsEnabled`: "true" when the switch is on, empty otherwise.
*/}}
{{- define "choregos.outpostsEnabled" -}}
{{- if eq (toString (.Values.global.outposts | default dict).enabled) "true" -}}
true
{{- end -}}
{{- end -}}

{{/*
The door's public origin (`CHOREGOS_OUTPOSTS_DOOR_URL`): `door.url`, else `https://` and the first of
the door's hostnames. The API refuses to start with outposts on and no origin, and on one that has
a path: the render refuses first, and names the value.
*/}}
{{- define "choregos.outpostsDoorUrl" -}}
{{- $door := (.Values.global.outposts | default dict).door | default dict -}}
{{- $hosts := compact ($door.hostnames | default list) -}}
{{- $url := $door.url | default "" -}}
{{- if and (not $url) (not $hosts) -}}
{{- fail "global.outposts.enabled=true needs the door's public origin: set global.outposts.door.hostnames (the door's own hostnames; the origin is https://<the first one>) or global.outposts.door.url (https://outposts.example.com). An outpost signs the URL it calls, and the API refuses to start without the origin it compares it with (CHOREGOS_OUTPOSTS_DOOR_URL)." -}}
{{- end -}}
{{- if not $url -}}{{- $url = printf "https://%s" (first $hosts) -}}{{- end -}}
{{- if not (regexMatch "^https?://[^\\s/@?#]+$" $url) -}}
{{- fail (printf "global.outposts.door.url: %q is not an origin: a scheme and a host, no path (https://outposts.example.com). The API refuses to start on it." $url) -}}
{{- end -}}
{{- $url -}}
{{- end -}}

{{/*
The Secret that holds the door's signing key: the one the operator names, else the one this chart
draws once and keeps (`templates/outposts-door.yaml`).
*/}}
{{- define "choregos.outpostsDoorKeySecret" -}}
{{- $key := ((.Values.global.outposts | default dict).door | default dict).signingKey | default dict -}}
{{- $key.existingSecret | default (printf "%s-outposts-door" .Release.Name) -}}
{{- end -}}

{{/*
What whoever places a call through an outpost reads: the switch, and the two quotas of calls in
flight (`outposts/rendezvous.py`, `place`). Part of `choregos.commonEnv` when outposts are on.
*/}}
{{- define "choregos.outpostsEnv" -}}
{{- $flight := (.Values.global.outposts | default dict).callsInFlight | default dict -}}
- name: CHOREGOS_OUTPOSTS
  value: "on"
- name: CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_OUTPOST
  value: {{ int64 ($flight.perOutpost | default 128) | quote }}
- name: CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_ORGANISATION
  value: {{ int64 ($flight.perOrganisation | default 512) | quote }}
{{- end -}}

{{/*
What the processes that serve the door or answer for it read, and no other: the API (it serves the
door unless it is split out, and it shows the keys to pin with an outpost and its enrolment token)
and the door's own Deployment. Never the workers: they sign no call. The signing key is read from
its Secret by reference, and is not optional: a door that could sign nothing would hand nothing over.
*/}}
{{- define "choregos.outpostsDoorEnv" -}}
{{- $door := (.Values.global.outposts | default dict).door | default dict -}}
{{- $key := $door.signingKey | default dict -}}
{{- $announced := $door.announcedKeys | default list -}}
{{- range $announced -}}
{{- if not (regexMatch "^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$" (toString .)) -}}
{{- fail (printf "global.outposts.door.announcedKeys: %q is not a public key: the unpadded base64url of its 32 raw bytes (43 characters), as the management API shows the door's keys. The API refuses to start on it." (toString .)) -}}
{{- end -}}
{{- end -}}
{{- if gt (len $announced) 3 -}}
{{- fail "global.outposts.door.announcedKeys: at most three keys beside the one that signs (an outpost's policy file pins four). The API refuses to start on more." -}}
{{- end -}}
{{- if ne (len (uniq $announced)) (len $announced) -}}
{{- fail "global.outposts.door.announcedKeys: a key is announced once. The API refuses to start on a key listed twice." -}}
{{- end -}}
- name: CHOREGOS_OUTPOSTS_DOOR_URL
  value: {{ include "choregos.outpostsDoorUrl" . | quote }}
- name: CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY
  valueFrom: { secretKeyRef: { name: {{ include "choregos.outpostsDoorKeySecret" . }}, key: {{ $key.key | default "signing-key" }} } }
- name: CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS
  value: {{ toJson $announced | quote }}
- name: CHOREGOS_OUTPOSTS_DOOR_RATE_PER_MINUTE
  value: {{ if hasKey $door "ratePerMinute" }}{{ int64 $door.ratePerMinute | quote }}{{ else }}"1200"{{ end }}
{{- end -}}

{{/*
The names the two templates above set: `global.extraEnv` may not set them a second time while
outposts are on. `only` = "all" (what the API receives) or "common" (what every process receives).
*/}}
{{- define "choregos.outpostsOwnedEnv" -}}
CHOREGOS_OUTPOSTS CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_OUTPOST CHOREGOS_OUTPOSTS_CALLS_IN_FLIGHT_PER_ORGANISATION
{{- if eq . "all" }} CHOREGOS_OUTPOSTS_DOOR_URL CHOREGOS_OUTPOSTS_DOOR_SIGNING_KEY CHOREGOS_OUTPOSTS_DOOR_ANNOUNCED_KEYS CHOREGOS_OUTPOSTS_DOOR_RATE_PER_MINUTE{{ end }}
{{- end -}}

{{/*
Dependencies: embedded by this chart, or provided from outside.

`global.<dep>.embedded` is the sub-chart's condition AND what these helpers read. The
flag lives under `global` because a sub-chart sees ONLY the parent's `global`: set
elsewhere, `charts/litellm` would not know which database host to use, and the rendering would be
right — in the parent chart only.
*/}}
{{- define "choregos.databaseHost" -}}
{{- if .Values.global.database.embedded -}}
{{ .Release.Name }}-postgresql
{{- else -}}
{{ .Values.global.database.host }}
{{- end -}}
{{- end -}}

{{- define "choregos.databasePasswordSecret" -}}
{{- if .Values.global.database.embedded -}}
{{ .Release.Name }}-postgresql
{{- else -}}
{{ .Values.global.database.passwordSecret }}
{{- end -}}
{{- end -}}

{{- define "choregos.temporalAddress" -}}
{{- if .Values.global.temporal.embedded -}}
{{ .Release.Name }}-temporal:7233
{{- else -}}
{{ .Values.global.temporal.address }}
{{- end -}}
{{- end -}}

{{- /* The namespace a tracing address points into (`<service>.<namespace>.svc[...]`), so the
network policies open the collector and Tempo where they actually run (S40-20e: on one operator's
dev cluster Tempo lives in `tempo`). A host that names no namespace keeps `monitoring`, the
bootstrap's (`infra/bootstrap/otel-collector.yaml`, `tempo.yaml`). */ -}}
{{- define "choregos.tracingNamespace" -}}
{{- $labels := splitList "." ((urlParse (. | default "http://x")).hostname | default "") -}}
{{- if and (ge (len $labels) 2) (index $labels 1) -}}
{{ index $labels 1 }}
{{- else -}}
monitoring
{{- end -}}
{{- end -}}

{{- define "choregos.gatewayUrl" -}}
{{- if .Values.global.gateway.embedded -}}
http://{{ .Release.Name }}-litellm:4000
{{- else -}}
{{ .Values.global.gateway.url }}
{{- end -}}
{{- end -}}

{{- define "choregos.memoryUrl" -}}
{{- if .Values.global.memory.embedded -}}
http://{{ .Release.Name }}-ecphoria:8432
{{- else -}}
{{ .Values.global.memory.url }}
{{- end -}}
{{- end -}}

{{/* Secrets: the ones you provide, or the ones the chart generates when it embeds the dependency. */}}
{{- define "choregos.gatewaySecret" -}}
{{- if .Values.global.gateway.secretRef -}}
{{ .Values.global.gateway.secretRef }}
{{- else -}}
{{ .Release.Name }}-gateway
{{- end -}}
{{- end -}}

{{/* The secret (key `api-key`) of the decision model's bearer (ADR 0121): Laya asks for it, LiteLLM sends it. */}}
{{- define "choregos.decisionsKeySecret" -}}
{{- if .Values.global.decisions.apiKeySecret -}}
{{ .Values.global.decisions.apiKeySecret }}
{{- else -}}
{{ .Release.Name }}-laya
{{- end -}}
{{- end -}}

{{- define "choregos.memoryTokenSecret" -}}
{{- if .Values.global.memory.tokenSecret -}}
{{ .Values.global.memory.tokenSecret }}
{{- else if .Values.global.memory.embedded -}}
{{ .Release.Name }}-memory
{{- end -}}
{{- end -}}

{{/*
Anti-affinity between the replicas of a same component. `global.antiAffinity`: `soft`
(preferred, the default — a single node still places its pods), `hard` (required: one
replica per node, or no pod) or `none`. A PodDisruptionBudget without anti-affinity
protects against a drain, not a failure: two replicas on the same node go down together.
*/}}
{{- define "choregos.antiAffinity" -}}
{{- $mode := .global.antiAffinity | default "soft" -}}
{{- if ne $mode "none" }}
affinity:
  podAntiAffinity:
    {{- if eq $mode "hard" }}
    requiredDuringSchedulingIgnoredDuringExecution:
      - topologyKey: kubernetes.io/hostname
        labelSelector:
          matchLabels: {{- toYaml .labels | nindent 12 }}
    {{- else }}
    preferredDuringSchedulingIgnoredDuringExecution:
      - weight: 100
        podAffinityTerm:
          topologyKey: kubernetes.io/hostname
          labelSelector:
            matchLabels: {{- toYaml .labels | nindent 14 }}
    {{- end }}
{{- end }}
{{- end -}}

{{/*
The alert rules, written once and served in both flavours (PrometheusRule and VMRule).
The PromQL is the same; only the packaging changes. Since 0.21 (ADR 0098) four alerts have English
names; the `legacy_alertname` label that carried their former names was removed in 0.24.0 (S45-49).
*/}}
{{- define "choregos.alertRules" -}}
- alert: ChoregosApi5xxErrors
  expr: |
    sum(rate(http_requests_total{job="choregos-api",status=~"5.."}[5m]))
    / clamp_min(sum(rate(http_requests_total{job="choregos-api"}[5m])), 0.001) > 0.01
  for: 10m
  labels: { severity: critical }
  annotations:
    summary: "More than 1 % of 5xx on the Choregos API"
    runbook_url: "https://github.com/VargaFoundation/choregos/blob/main/docs/runbooks/api-5xx.md"
- alert: ChoregosBacklogTemporal
  expr: sum(temporal_task_queue_backlog{namespace="default"}) > 100
  for: 10m
  labels: { severity: warning }
  annotations:
    summary: "Temporal queue saturated: stages are waiting"
- alert: ChoregosRunPending
  expr: sum(kube_pod_status_phase{phase="Pending",namespace=~"proj-.*-runners"}) > 0
  for: 15m
  labels: { severity: warning }
  annotations:
    summary: "A PipelineRun has been waiting for 15 min (autoscaling down?)"
- alert: ChoregosDailyBudgetHigh
  expr: choregos_project_daily_cost_usd / choregos_project_daily_budget_usd > 0.8
  for: 5m
  labels: { severity: warning }
  annotations:
    summary: "A project's daily budget is more than 80 % consumed"
- alert: ChoregosTrainFrozen
  expr: choregos_train_frozen == 1
  for: 2h
  labels: { severity: warning }
  annotations:
    summary: "A release train has been frozen for 2 hours"
    runbook_url: "https://github.com/VargaFoundation/choregos/blob/main/docs/runbooks/train-frozen.md"
{{- end }}

{{/*
The rule of the outposts' door (ADR 0148 §7), rendered with the others while outposts are on. A
second poll under a key that already has one waiting is refused by the door, which writes one audit
event a minute at most for the instance; `/metrics` counts those events per organisation. Its own
template: `choregos.alertRules` stays plain YAML, which a test reads as such.
*/}}
{{- define "choregos.outpostsAlertRules" -}}
- alert: ChoregosOutpostPollConflict
  expr: max by (org) (increase(choregos_outpost_poll_conflicts_total[15m])) > 0
  labels: { severity: critical }
  annotations:
    summary: "A second poll arrived under an outpost instance's key: two holders of one key is what a stolen key looks like"
    description: "Organisation {{ "{{" }} $labels.org {{ "}}" }}: the door refused the second poll (409 poll_conflict) and wrote `outpost.poll_conflict` in the organisation's audit log, which names the instance. A daemon restarted while its former poll still held the slot raises it too. If the key may be held twice, revoke the instance: its signature opens nothing from its next request."
{{- end }}

{{/*
Image pull secrets, for private registries.

The chart declared none (`rg imagePullSecrets charts/`: zero). As long as all the images
are public, nobody sees it. As soon as one no longer is — an enterprise edition, a private
Harbor mirror, a home-made image — the deployment fails with `ImagePullBackOff`, and the only way out
was to patch the service account by hand, outside GitOps.

The name is global (`global.imagePullSecrets`) because the question arises for ALL the chart's pods,
and a tenant that sets one secret sets one for the whole set.
*/}}
{{- define "choregos.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Whether the chart deploys its own `buildkitd` (S41-13): BuildKit is the builder, there is a
repository to push to, and no other daemon is named. Reads `global` only, so the orchestrator's
subchart asks the same question and gets the same answer.
*/}}
{{- define "choregos.buildkitdDeployed" -}}
{{- $images := .Values.global.images | default dict -}}
{{- $b := .Values.global.buildkitd | default dict -}}
{{- if and (ne $b.enabled false) (eq ($images.hostedBuilder | default "buildkit") "buildkit") $images.buildRegistry (not $images.buildkitAddress) -}}
true
{{- end -}}
{{- end -}}

{{/* The address `buildctl` dials: the operator's daemon, else the chart's, else none. */}}
{{- define "choregos.buildkitAddress" -}}
{{- $images := .Values.global.images | default dict -}}
{{- if $images.buildkitAddress -}}
{{- $images.buildkitAddress -}}
{{- else if include "choregos.buildkitdDeployed" . -}}
{{- printf "tcp://choregos-buildkitd:%d" (int ((.Values.global.buildkitd | default dict).port | default 1234)) -}}
{{- end -}}
{{- end -}}
