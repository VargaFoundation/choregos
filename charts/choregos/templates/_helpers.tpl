{{/* Nom court et labels communs à tous les objets de la plateforme. */}}
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

{{/* Référence d'image : par digest dès que `global.imageDigest` est fourni (exigence Kyverno). */}}
{{- define "choregos.image" -}}
{{- $global := .global -}}
{{- $repo := printf "%s/%s" $global.imageRegistry .image.repository -}}
{{- if .image.digest -}}
{{ $repo }}@{{ .image.digest }}
{{- else if $global.imageDigest -}}
{{ $repo }}@{{ $global.imageDigest }}
{{- else -}}
{{ $repo }}:{{ default $global.imageTag .image.tag }}
{{- end -}}
{{- end -}}

{{/* Variables d'environnement communes : configuration 12-factor, secrets par référence. */}}
{{- define "choregos.commonEnv" -}}
- name: CHOREGOS_ENV
  value: {{ .Values.global.environment | quote }}
- name: CHOREGOS_LOG_LEVEL
  value: {{ .Values.global.logLevel | quote }}
- name: CHOREGOS_FAKES
  value: {{ .Values.global.fakes | quote }}
- name: CHOREGOS_TEMPORAL_ADDRESS
  value: {{ include "choregos.temporalAddress" . | quote }}
- name: CHOREGOS_TEMPORAL_NAMESPACE
  value: {{ .Values.global.temporal.namespace | quote }}
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
- name: CHOREGOS_PUBLIC_URL
  value: {{ .Values.global.publicUrl | default (printf "https://app.%s" .Values.global.domain) | quote }}
- name: CHOREGOS_API_URL
  value: {{ .Values.global.apiUrl | default (printf "https://api.%s" .Values.global.domain) | quote }}
{{- /* Le pool de connexions, BORNÉ : `pool_size` seul laissait SQLAlchemy ouvrir dix
       connexions de débordement par processus sans limite de temps d'attente (état des lieux
       du 2026-09-24). Chaque réplique de l'API et chaque worker tient au plus size+maxOverflow
       connexions ; à multiplier par les répliques pour dimensionner `max_connections`. */}}
- name: CHOREGOS_DB_POOL_SIZE
  value: {{ .Values.global.database.pool.size | quote }}
- name: CHOREGOS_DB_MAX_OVERFLOW
  value: {{ .Values.global.database.pool.maxOverflow | quote }}
- name: CHOREGOS_DB_POOL_TIMEOUT_S
  value: {{ .Values.global.database.pool.timeoutSeconds | quote }}
{{- with (include "choregos.databasePasswordSecret" .) }}
{{- /* Le mot de passe vient d'un secret posé par un opérateur (Zalando : clé `password`),
       l'URL est composée ici — Kubernetes développe `$(VAR)` d'une variable déclarée avant. */}}
- name: CHOREGOS_DATABASE_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ . }}
      key: password
- name: CHOREGOS_DATABASE_URL
  {{- /* Embarqué : le rôle applicatif sans SUPERUSER créé à l'initdb. Externe : le rôle que
         l'opérateur a créé (Zalando, CNPG), déjà sans SUPERUSER — `choregos_app` n'y existe pas. */}}
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
Les dépendances : embarquées par ce chart, ou fournies par l'extérieur.

`global.<dep>.embedded` est la condition du sous-chart ET ce que lisent ces helpers. Le
drapeau vit sous `global` parce qu'un sous-chart ne voit QUE `global` du parent : posé
ailleurs, `charts/litellm` ne saurait pas quel hôte de base employer, et le rendu serait
juste — dans le chart parent seulement.
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

{{/* Secrets : ceux qu'on fournit, ou ceux que le chart génère quand il embarque la dépendance. */}}
{{- define "choregos.gatewaySecret" -}}
{{- if .Values.global.gateway.secretRef -}}
{{ .Values.global.gateway.secretRef }}
{{- else -}}
{{ .Release.Name }}-gateway
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
Anti-affinité entre les répliques d'un même composant. `global.antiAffinity` : `soft`
(préférée, le défaut — un mono-nœud place quand même ses pods), `hard` (exigée : une
réplique par nœud, ou pas de pod) ou `none`. Un PodDisruptionBudget sans anti-affinité
protège d'un drain, pas d'une panne : deux répliques sur le même nœud tombent ensemble.
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
Les règles d'alerte, écrites une fois et servies aux deux saveurs (PrometheusRule et VMRule).
Le PromQL est le même ; seul l'emballage change.
*/}}
{{- define "choregos.reglesDAlerte" -}}
- alert: ChoregosApiErreurs5xx
  expr: |
    sum(rate(http_requests_total{job="choregos-api",status=~"5.."}[5m]))
    / clamp_min(sum(rate(http_requests_total{job="choregos-api"}[5m])), 0.001) > 0.01
  for: 10m
  labels: { severity: critical }
  annotations:
    summary: "Plus de 1 % de 5xx sur l'API Choregos"
    runbook_url: "https://github.com/VargaFoundation/choregos/blob/main/docs/runbooks/api-5xx.md"
- alert: ChoregosBacklogTemporal
  expr: sum(temporal_task_queue_backlog{namespace="default"}) > 100
  for: 10m
  labels: { severity: warning }
  annotations:
    summary: "File Temporal saturée : les étapes attendent"
- alert: ChoregosRunEnAttente
  expr: sum(kube_pod_status_phase{phase="Pending",namespace=~"proj-.*-runners"}) > 0
  for: 15m
  labels: { severity: warning }
  annotations:
    summary: "Un PipelineRun attend depuis 15 min (autoscaling en panne ?)"
- alert: ChoregosBudgetJournalier
  expr: choregos_project_daily_cost_usd / choregos_project_daily_budget_usd > 0.8
  for: 5m
  labels: { severity: warning }
  annotations:
    summary: "Budget journalier d'un projet consommé à plus de 80 %"
- alert: ChoregosTrainGele
  expr: choregos_train_frozen == 1
  for: 2h
  labels: { severity: warning }
  annotations:
    summary: "Un release train est gelé depuis 2 heures"
    runbook_url: "https://github.com/VargaFoundation/choregos/blob/main/docs/runbooks/train-gele.md"
{{- end }}

{{/*
Les secrets de tirage d'image, pour les registres privés.

Le chart n'en déclarait aucun (`rg imagePullSecrets charts/` : zéro). Tant que toutes les images
sont publiques, personne ne le voit. Dès qu'une ne l'est plus — une édition entreprise, un miroir
Harbor privé, une image maison — le déploiement échoue en `ImagePullBackOff`, et la seule issue
était de patcher le compte de service à la main, hors GitOps.

Le nom est global (`global.imagePullSecrets`) parce que la question se pose pour TOUS les pods du
chart, et qu'un locataire qui pose un secret en pose un pour l'ensemble.
*/}}
{{- define "choregos.imagePullSecrets" -}}
{{- with .Values.global.imagePullSecrets }}
imagePullSecrets:
{{- range . }}
  - name: {{ . }}
{{- end }}
{{- end }}
{{- end }}
