{{- define "choregos-temporal.name" -}}
{{- include "choregos.temporalServiceName" . -}}
{{- end -}}
{{- define "choregos-temporal.serverSecret" -}}
{{- .Values.tls.serverSecretName | default (printf "%s-tls" (include "choregos-temporal.name" .)) -}}
{{- end -}}
{{- define "choregos-temporal.clientSecret" -}}
{{- .Values.tls.clientSecretName | default (printf "%s-client-tls" (include "choregos-temporal.name" .)) -}}
{{- end -}}
{{- define "choregos-temporal.claim" -}}
{{- .Values.persistence.existingClaim | default (include "choregos-temporal.name" .) -}}
{{- end -}}
