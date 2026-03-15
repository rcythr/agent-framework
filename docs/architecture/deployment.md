## Docker Images

## `Dockerfile.gateway`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway/ ./gateway/
COPY shared/ ./shared/
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "3000"]
```

## `Dockerfile.worker`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY worker/ ./worker/
COPY shared/ ./shared/
CMD ["python", "-m", "worker.main"]
```

---

## Kubernetes Manifests

## `k8s/gateway-deployment.yaml`

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: pi-agent-gateway
  namespace: pi-agents
spec:
  replicas: 2
  selector:
    matchLabels:
      app: pi-agent-gateway
  template:
    metadata:
      labels:
        app: pi-agent-gateway
    spec:
      serviceAccountName: pi-agent-gateway
      containers:
        - name: gateway
          image: your-registry/pi-agent-gateway:latest
          ports:
            - containerPort: 3000
          livenessProbe:
            httpGet:
              path: /healthz
              port: 3000
          env:
            - name: GITLAB_WEBHOOK_SECRET
              valueFrom:
                secretKeyRef:
                  name: gitlab-creds
                  key: webhook-secret
            - name: PI_AGENT_IMAGE
              value: your-registry/pi-agent-worker:latest
            - name: LLM_ENDPOINT
              value: https://api.openai.com/v1
            - name: AGENT_CONFIG_DIR
              value: .agents   # override per deployment; must be repo-relative, no leading slash
---
apiVersion: v1
kind: Service
metadata:
  name: pi-agent-gateway
  namespace: pi-agents
spec:
  selector:
    app: pi-agent-gateway
  ports:
    - port: 80
      targetPort: 3000
```

## `k8s/rbac.yaml`

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: pi-agent-gateway
  namespace: pi-agents
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: job-spawner
  namespace: pi-agents
rules:
  - apiGroups: ["batch"]
    resources: ["jobs"]
    verbs: ["create", "get", "watch", "delete"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: gateway-job-spawner
  namespace: pi-agents
subjects:
  - kind: ServiceAccount
    name: pi-agent-gateway
roleRef:
  kind: Role
  name: job-spawner
  apiGroup: rbac.authorization.k8s.io
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: pi-agent-worker
  namespace: pi-agents
```

## `k8s/secrets.yaml`

```yaml
# Apply with: kubectl apply -f k8s/secrets.yaml
# Values should be base64-encoded: echo -n "value" | base64
apiVersion: v1
kind: Secret
metadata:
  name: gitlab-creds
  namespace: pi-agents
data:
  token: <base64-encoded-gitlab-token>
  webhook-secret: <base64-encoded-webhook-secret>
---
apiVersion: v1
kind: Secret
metadata:
  name: llm-creds
  namespace: pi-agents
data:
  api-key: <base64-encoded-llm-api-key>
```

---

## GitLab Configuration

## Webhook Setup

In your GitLab project, go to **Settings → Webhooks → Add new webhook**:

| Field | Value |
|---|---|
| URL | `https://pi-agent-gateway.your-domain.com/webhook/gitlab` |
| Secret token | matches `GITLAB_WEBHOOK_SECRET` in the K8s secret |
| Trigger: Push events | ✅ |
| Trigger: Merge request events | ✅ |
| Trigger: Comments | ✅ |

## Manual Trigger via CI — `.gitlab-ci.yml`

```yaml
trigger-pi-agent:
  stage: review
  when: manual
  variables:
    TASK: "handle_comment"
    CONTEXT: '{"instruction": "Refactor the auth module for readability"}'
  script:
    - |
      curl -sf -X POST https://pi-agent-gateway.your-domain.com/trigger \
        -H "Content-Type: application/json" \
        -d "{\"task\": \"$TASK\", \"project_id\": $CI_PROJECT_ID, \"context\": $CONTEXT}"
```

---
