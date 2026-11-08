{{/*
Expand the name of the chart.
*/}}
{{- define "memchentic.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "memchentic.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart label.
*/}}
{{- define "memchentic.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels.
*/}}
{{- define "memchentic.labels" -}}
helm.sh/chart: {{ include "memchentic.chart" . }}
{{ include "memchentic.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels.
*/}}
{{- define "memchentic.selectorLabels" -}}
app.kubernetes.io/name: {{ include "memchentic.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Postgres component labels.
*/}}
{{- define "memchentic.postgres.labels" -}}
{{ include "memchentic.labels" . }}
app.kubernetes.io/component: postgres
{{- end }}

{{- define "memchentic.postgres.selectorLabels" -}}
app.kubernetes.io/name: {{ include "memchentic.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/component: postgres
{{- end }}

{{/*
Validate secrets.mode and required fields.
*/}}
{{- define "memchentic.validateSecrets" -}}
{{- $mode := .Values.secrets.mode | default "existing" }}
{{- if not (or (eq $mode "inline") (eq $mode "existing") (eq $mode "externalSecret")) }}
{{- fail (printf "secrets.mode must be inline, existing, or externalSecret (got %q)" $mode) }}
{{- end }}
{{- if not .Values.config.baseUrl }}
{{- fail "config.baseUrl is required (public https origin)" }}
{{- end }}
{{- if eq $mode "existing" }}
{{- if not .Values.secrets.existingSecret }}
{{- fail "secrets.mode=existing requires secrets.existingSecret" }}
{{- end }}
{{- end }}
{{- if eq $mode "inline" }}
{{- $required := list "ADMIN_PASSWORD" "ADMIN_API_KEY" "POSTGRES_PASSWORD" "POSTGRES_APP_PASSWORD" "ENCRYPTION_KEY" }}
{{- range $required }}
{{- $val := index $.Values.secrets.inline . | default "" }}
{{- if or (not $val) (contains "change-me" ($val | lower)) (lt (len $val) 16) }}
{{- fail (printf "secrets.mode=inline requires a strong secrets.inline.%s (16+ chars, no change-me)" .) }}
{{- end }}
{{- end }}
{{- end }}
{{- if eq $mode "externalSecret" }}
{{- if not .Values.secrets.externalSecret.secretStoreRef.name }}
{{- fail "secrets.mode=externalSecret requires secrets.externalSecret.secretStoreRef.name" }}
{{- end }}
{{- range $secretKey, $ref := .Values.secrets.externalSecret.data }}
{{- if not $ref.key }}
{{- fail (printf "secrets.externalSecret.data.%s.key is required" $secretKey) }}
{{- end }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Resolved Secret name for envFrom / postgres.
*/}}
{{- define "memchentic.secretName" -}}
{{- include "memchentic.validateSecrets" . }}
{{- $mode := .Values.secrets.mode | default "inline" }}
{{- if eq $mode "existing" }}
{{- .Values.secrets.existingSecret }}
{{- else }}
{{- printf "%s-env" (include "memchentic.fullname" .) }}
{{- end }}
{{- end }}

{{/*
Postgres Service DNS name (in-cluster) or configured external hostname.
*/}}
{{- define "memchentic.postgresHostname" -}}
{{- if .Values.postgres.enabled }}
{{- printf "%s-postgres" (include "memchentic.fullname" .) }}
{{- else }}
{{- $host := .Values.config.postgresHostname | default "" | trim }}
{{- if not $host }}
{{- fail "postgres.enabled=false requires config.postgresHostname" }}
{{- end }}
{{- $host }}
{{- end }}
{{- end }}

{{/*
Service account name.
*/}}
{{- define "memchentic.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "memchentic.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}
