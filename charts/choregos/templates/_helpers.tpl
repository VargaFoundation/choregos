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
  value: {{ .Values.global.temporal.address | quote }}
- name: CHOREGOS_TEMPORAL_NAMESPACE
  value: {{ .Values.global.temporal.namespace | quote }}
- name: CHOREGOS_GATEWAY_URL
  value: {{ .Values.global.gateway.url | quote }}
- name: CHOREGOS_MEMORY_URL
  value: {{ .Values.global.memory.url | quote }}
- name: CHOREGOS_OBJECT_STORE_URL
  value: {{ .Values.global.objectStore.url | quote }}
- name: CHOREGOS_PUBLIC_URL
  value: {{ .Values.global.publicUrl | default (printf "https://app.%s" .Values.global.domain) | quote }}
- name: CHOREGOS_API_URL
  value: {{ .Values.global.apiUrl | default (printf "https://api.%s" .Values.global.domain) | quote }}
- name: CHOREGOS_DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.global.database.secretRef }}
      key: url
{{- end -}}
