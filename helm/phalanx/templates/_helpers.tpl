{{/*
Expand the name of the chart.
*/}}
{{- define "phalanx.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Namespace to deploy into.
*/}}
{{- define "phalanx.namespace" -}}
{{- .Values.namespace.name | default "pi-agents" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "phalanx.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
OAuth2 provider value — falls back to the global provider if not explicitly set.
*/}}
{{- define "phalanx.oauth2Provider" -}}
{{- if .Values.oauth2proxy.provider -}}
{{- .Values.oauth2proxy.provider -}}
{{- else -}}
{{- .Values.provider -}}
{{- end -}}
{{- end }}
