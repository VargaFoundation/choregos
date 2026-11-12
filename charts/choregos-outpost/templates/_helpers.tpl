{{/* The name every object of one outpost carries: the release's, with the chart's when it does not hold it. */}}
{{- define "choregos-outpost.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 52 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 52 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 52 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "choregos-outpost.labels" -}}
app.kubernetes.io/name: choregos-outpost
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: choregos-outpost
{{- end -}}

{{/* What selects the daemon's pods, and nothing else of the release. */}}
{{- define "choregos-outpost.selectorLabels" -}}
app.kubernetes.io/name: choregos-outpost
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: daemon
{{- end -}}

{{/* What selects the pods of one server the operator runs beside the daemon: (dict "root" $ "name" <its name>). */}}
{{- define "choregos-outpost.serverSelectorLabels" -}}
app.kubernetes.io/name: choregos-outpost-server
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ printf "server-%s" .name }}
{{- end -}}

{{/* The daemon's image: by digest when the operator gives one (ADR 0147 §9), by tag otherwise. */}}
{{- define "choregos-outpost.image" -}}
{{- $image := .Values.image -}}
{{- $repository := printf "%s/%s" $image.registry $image.repository -}}
{{- if $image.digest -}}
{{- if not (regexMatch "^sha256:[a-f0-9]{64}$" $image.digest) -}}
{{- fail (printf "image.digest: %q is not a digest (sha256: and 64 hexadecimal characters)" $image.digest) -}}
{{- end -}}
{{ $repository }}@{{ $image.digest }}
{{- else -}}
{{ $repository }}:{{ $image.tag | default .Chart.AppVersion }}
{{- end -}}
{{- end -}}

{{/* Where things are in the pod. The state directory and the audit log's are the only writable ones. */}}
{{- define "choregos-outpost.stateDir" -}}/var/lib/choregos-outpost{{- end -}}
{{- define "choregos-outpost.configDir" -}}/etc/choregos-outpost{{- end -}}

{{/* The Secret that holds the enrolment token: the one the operator names, else `<fullname>-enrolment`. */}}
{{- define "choregos-outpost.enrolmentSecret" -}}
{{- (.Values.enrolment | default dict).existingSecret | default (printf "%s-enrolment" (include "choregos-outpost.fullname" .)) -}}
{{- end -}}

{{/* The port a parsed URL names: its own, else its scheme's. */}}
{{- define "choregos-outpost.portOf" -}}
{{- $port := regexFind ":[0-9]+$" (.host | default "") | trimPrefix ":" -}}
{{- $port | default (ternary "443" "80" (eq .scheme "https")) -}}
{{- end -}}

{{/*
The peers a NetworkPolicy can name for a host, as a JSON list: the addresses the operator gives; else
the address itself; else the pods of the namespace a Service's name carries
(`<service>.<namespace>[.svc…]`, this namespace for a bare name). An empty list when the host is a
name outside the cluster: a NetworkPolicy cannot name a host.
(dict "host" … "cidrs" … "namespace" … "podLabels" … "release" <this namespace>)
*/}}
{{- define "choregos-outpost.peers" -}}
{{- $peers := list -}}
{{- $namespace := .namespace | default "" -}}
{{- $host := .host | default "" -}}
{{- if .cidrs -}}
{{- range .cidrs -}}{{- $peers = append $peers (dict "ipBlock" (dict "cidr" (toString .))) -}}{{- end -}}
{{- else if and (not $namespace) (regexMatch "^[0-9]+(\\.[0-9]+){3}$" $host) -}}
{{- $peers = append $peers (dict "ipBlock" (dict "cidr" (printf "%s/32" $host))) -}}
{{- else -}}
{{- $labels := splitList "." $host -}}
{{- if and (not $namespace) $host -}}
{{- if eq (len $labels) 1 -}}
{{- $namespace = .release -}}
{{- else if eq (len $labels) 2 -}}
{{- $namespace = index $labels 1 -}}
{{- else if eq (index $labels 2) "svc" -}}
{{- $namespace = index $labels 1 -}}
{{- end -}}
{{- end -}}
{{- if $namespace -}}
{{- $peer := dict "namespaceSelector" (dict "matchLabels" (dict "kubernetes.io/metadata.name" $namespace)) -}}
{{- if .podLabels -}}{{- $_ := set $peer "podSelector" (dict "matchLabels" .podLabels) -}}{{- end -}}
{{- $peers = append $peers $peer -}}
{{- end -}}
{{- end -}}
{{- toJson $peers -}}
{{- end -}}

{{/*
The policy file (ADR 0147 §6), as the daemon reads it: `outpost-policy.schema.json` of the platform's
contracts. Rendered from `.Values.policy` field by field, so that nothing else reaches the ConfigMap:
a credential is a Secret the operator names, mounted as a file whose path is written as
`auth.from_file`; the chart's own `network` is left out.

The daemon does not start on an invalid policy, `door_keys` included: an outpost that cannot check
who orders it does not serve. What this chart can check is refused at render, by the field's name,
rather than found in a pod that never starts.
*/}}
{{- define "choregos-outpost.policy" -}}
{{- $policy := .Values.policy | default dict -}}
{{- range $field, $_ := $policy -}}
{{- if not (has $field (list "control_plane" "door_keys" "endpoints" "concurrency" "audit")) -}}
{{- fail (printf "policy.%s: the policy file has no such field (control_plane, door_keys, endpoints, concurrency, audit)" $field) -}}
{{- end -}}
{{- end -}}
{{- $controlPlane := $policy.control_plane | default "" | toString -}}
{{- if not $controlPlane -}}
{{- fail "policy.control_plane is required: the door's origin, as the console gives it with an enrolment token (https://outposts.example.com). It is the one address this outpost dials." -}}
{{- end -}}
{{- if not (regexMatch "^https?://[^\\s/@?#]+$" $controlPlane) -}}
{{- fail (printf "policy.control_plane: %q is not an origin: a scheme and a host, with a port if any, and no path (https://outposts.example.com)" $controlPlane) -}}
{{- end -}}
{{- $keys := $policy.door_keys | default list -}}
{{- if not $keys -}}
{{- fail "policy.door_keys is required: the door's public keys, as the console prints them with the outpost and with its enrolment token. An outpost makes only calls signed by a key it pins (ADR 0148 §13): without one the daemon does not start." -}}
{{- end -}}
{{- if gt (len $keys) 4 -}}
{{- fail "policy.door_keys: four keys at most (the one that signs, and room for a rotation)" -}}
{{- end -}}
{{- range $keys -}}
{{- if not (regexMatch "^[A-Za-z0-9_-]{42}[AEIMQUYcgkosw048]$" (toString .)) -}}
{{- fail (printf "policy.door_keys: %q is not a key: the unpadded base64url of its 32 raw bytes, 43 characters, as the console prints it" (toString .)) -}}
{{- end -}}
{{- end -}}
{{- if ne (len (uniq $keys)) (len $keys) -}}
{{- fail "policy.door_keys: a key is pinned once" -}}
{{- end -}}
{{- $audit := $policy.audit | default dict -}}
{{- range $field, $_ := $audit -}}
{{- if ne $field "file" -}}{{- fail (printf "policy.audit.%s: the policy file has no such field (file)" $field) -}}{{- end -}}
{{- end -}}
{{- $auditFile := $audit.file | default "" | toString -}}
{{- if not (regexMatch "^/[^/]+/.*[^/]$" $auditFile) -}}
{{- fail (printf "policy.audit.file: %q is not an absolute path to a file in a directory of its own (/var/log/choregos-outpost/audit.jsonl): that directory is mounted for the log" $auditFile) -}}
{{- end -}}
{{- $endpoints := dict -}}
{{- range $alias, $endpoint := ($policy.endpoints | default dict) -}}
{{- $where := printf "policy.endpoints.%s" $alias -}}
{{- if not (regexMatch "^[a-z][a-z0-9-]{0,62}$" $alias) -}}
{{- fail (printf "%s: an alias is lower case letters, digits and hyphens, a letter first, 63 characters at most" $where) -}}
{{- end -}}
{{- range $field, $_ := $endpoint -}}
{{- if not (has $field (list "url" "tools" "writes" "timeout_s" "max_payload_kb" "auth" "network")) -}}
{{- fail (printf "%s.%s: an endpoint has no such field (url, tools, writes, timeout_s, max_payload_kb, auth; network is this chart's)" $where $field) -}}
{{- end -}}
{{- end -}}
{{- if not (regexMatch "^https?://[^\\s@?#]+$" ($endpoint.url | default "" | toString)) -}}
{{- fail (printf "%s.url is required: the server's MCP endpoint (http://tracker-mcp.tools.svc:8080/mcp), without credentials, query or fragment" $where) -}}
{{- end -}}
{{- if not (kindIs "slice" $endpoint.tools) -}}
{{- fail (printf "%s.tools is required: the tool names a call may use, `*` for any run of characters; an empty list serves none" $where) -}}
{{- end -}}
{{- range $endpoint.tools -}}
{{- if not (regexMatch "^[A-Za-z0-9_.*-]{1,128}$" (toString .)) -}}
{{- fail (printf "%s.tools: %q is not a tool pattern (letters, digits, `_`, `.`, `-` and `*`)" $where (toString .)) -}}
{{- end -}}
{{- end -}}
{{- $rendered := dict "url" $endpoint.url "tools" $endpoint.tools -}}
{{- if hasKey $endpoint "writes" -}}
{{- if not (has $endpoint.writes (list "deny" "allow")) -}}
{{- fail (printf "%s.writes: %q is not deny or allow" $where (toString $endpoint.writes)) -}}
{{- end -}}
{{- $_ := set $rendered "writes" $endpoint.writes -}}
{{- end -}}
{{- if hasKey $endpoint "timeout_s" -}}
{{- if or (lt (float64 $endpoint.timeout_s) 1.0) (gt (float64 $endpoint.timeout_s) 300.0) -}}
{{- fail (printf "%s.timeout_s: between 1 and 300 seconds" $where) -}}
{{- end -}}
{{- $_ := set $rendered "timeout_s" $endpoint.timeout_s -}}
{{- end -}}
{{- if hasKey $endpoint "max_payload_kb" -}}
{{- if or (lt (int $endpoint.max_payload_kb) 1) (gt (int $endpoint.max_payload_kb) 1024) -}}
{{- fail (printf "%s.max_payload_kb: between 1 and 1024 KiB" $where) -}}
{{- end -}}
{{- $_ := set $rendered "max_payload_kb" (int $endpoint.max_payload_kb) -}}
{{- end -}}
{{- with $endpoint.auth -}}
{{- range $field, $_ := . -}}
{{- if not (has $field (list "header" "secretName" "secretKey")) -}}
{{- fail (printf "%s.auth.%s: a credential is never a value here. Name the Secret that holds it (auth.secretName, auth.secretKey): it is mounted as a file, and the policy's auth.from_file is written with its path" $where $field) -}}
{{- end -}}
{{- end -}}
{{- if or (not .secretName) (not .secretKey) -}}
{{- fail (printf "%s.auth needs secretName and secretKey: the Secret of this namespace that holds the credential, and its key" $where) -}}
{{- end -}}
{{- if not (regexMatch "^[A-Za-z0-9._-]+$" (toString .secretKey)) -}}
{{- fail (printf "%s.auth.secretKey: %q is not a key of a Secret" $where (toString .secretKey)) -}}
{{- end -}}
{{- $auth := dict "from_file" (printf "%s/credentials/%s/%s" (include "choregos-outpost.configDir" $) $alias .secretKey) -}}
{{- if .header -}}
{{- if not (regexMatch "^[A-Za-z0-9-]{1,64}$" (toString .header)) -}}
{{- fail (printf "%s.auth.header: %q is not a header name" $where (toString .header)) -}}
{{- end -}}
{{- $_ := set $auth "header" .header -}}
{{- end -}}
{{- $_ := set $rendered "auth" $auth -}}
{{- end -}}
{{- $_ := set $endpoints $alias $rendered -}}
{{- end -}}
{{- $document := dict "control_plane" $controlPlane "door_keys" $keys "endpoints" $endpoints "audit" (dict "file" $auditFile) -}}
{{- if hasKey $policy "concurrency" -}}
{{- if or (lt (int $policy.concurrency) 1) (gt (int $policy.concurrency) 64) -}}
{{- fail "policy.concurrency: between 1 and 64 calls at once" -}}
{{- end -}}
{{- $_ := set $document "concurrency" (int $policy.concurrency) -}}
{{- end -}}
{{- toYaml $document -}}
{{- end -}}

{{/*
What the daemon reads from its environment, for `enrol` (the init container) and for `run` alike:
where its files are, how it reaches the door, how long it lets calls finish.
*/}}
{{- define "choregos-outpost.env" -}}
{{- $config := include "choregos-outpost.configDir" . -}}
{{- $proxy := .Values.proxy | default dict -}}
{{- $ca := .Values.ca | default dict -}}
- { name: CHOREGOS_OUTPOST_POLICY, value: {{ printf "%s/policy/policy.yaml" $config | quote }} }
- { name: CHOREGOS_OUTPOST_STATE_DIR, value: {{ include "choregos-outpost.stateDir" . | quote }} }
- { name: CHOREGOS_OUTPOST_LOG_LEVEL, value: {{ .Values.logLevel | default "INFO" | quote }} }
- { name: CHOREGOS_OUTPOST_GRACE_S, value: {{ .Values.graceSeconds | toString | quote }} }
{{- if and $proxy.url $proxy.existingSecret }}
{{- fail "proxy: url or existingSecret, not both" }}
{{- end }}
{{- if $proxy.url }}
{{- if contains "@" $proxy.url }}
{{- fail "proxy.url carries a user and a password: that is a secret. Put the whole URL in a Secret and name it (proxy.existingSecret, proxy.key)" }}
{{- end }}
- { name: CHOREGOS_OUTPOST_PROXY, value: {{ $proxy.url | quote }} }
{{- else if $proxy.existingSecret }}
- name: CHOREGOS_OUTPOST_PROXY
  valueFrom: { secretKeyRef: { name: {{ $proxy.existingSecret }}, key: {{ $proxy.key | default "url" }} } }
{{- end }}
{{- if and $ca.configMap $ca.secret }}
{{- fail "ca: configMap or secret, not both" }}
{{- end }}
{{- if or $ca.configMap $ca.secret }}
- { name: CHOREGOS_OUTPOST_CA_FILE, value: {{ printf "%s/ca/%s" $config ($ca.key | default "ca.crt") | quote }} }
{{- end }}
{{- end -}}

{{/* The security context of every container of this chart (ADR 0096 §2, the model of a plugin server's pod). */}}
{{- define "choregos-outpost.containerSecurityContext" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
capabilities: { drop: ["ALL"] }
seccompProfile: { type: RuntimeDefault }
{{- end -}}

{{/*
The init container's script. The daemon has no "enrol if needed": `enrol` always presents a token,
and a token is spent by its first use. So the script looks first: an instance that already enrolled
(`instance.json` beside its keys) is left alone, and `enrol` runs only for one that has no identity
yet, or whose enrolment answer was lost (it then enrols again with the keys it has, ADR 0148 §3).

It also puts the keys back to the mode the daemon wrote them with. When the kubelet sets a volume's
group (`fsGroup`) it makes every file group-writable, and the daemon refuses a private key its group
can write. `fsGroupChangePolicy: OnRootMismatch` keeps the kubelet from doing it at every start; this
covers the times it does.
*/}}
{{- define "choregos-outpost.enrolScript" -}}
set -eu
identity="${CHOREGOS_OUTPOST_IDENTITY_DIR:-${CHOREGOS_OUTPOST_STATE_DIR}/identity}"
for key in "$identity/signing.key" "$identity/sealing.key"; do
  if [ -f "$key" ]; then chmod 600 "$key"; fi
done
if [ -f "$identity/instance.json" ]; then
  echo "choregos-outpost: this instance is already enrolled, nothing to do."
  exit 0
fi
if [ ! -s "$ENROLMENT_TOKEN_FILE" ]; then
  echo "choregos-outpost: this instance has no identity yet, and no enrolment token is mounted." >&2
  echo "Ask the console for an enrolment token of this outpost (single use, valid 15 minutes), then:" >&2
  echo "  kubectl -n {{ .Release.Namespace }} create secret generic {{ include "choregos-outpost.enrolmentSecret" . }} --from-literal={{ (.Values.enrolment | default dict).key | default "token" }}=<the token>" >&2
  echo "This container starts again by itself and takes it." >&2
  exit 1
fi
exec choregos-outpost enrol --token-file "$ENROLMENT_TOKEN_FILE"
{{- end -}}
