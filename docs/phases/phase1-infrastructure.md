# Phase 1 — Infrastructure Foundation

## Goal
A fully self-contained local environment running on KIND (Kubernetes IN Docker) that includes:
- A **GitLab CE** instance running inside the cluster, used for all E2E and integration testing
- The **pi-agent gateway** deployed and able to receive GitLab webhook events and spawn K8s Jobs
- A **local image registry** so both the gateway and worker images are available to cluster nodes without an external registry account

No external services, tunnels, or cloud accounts are required. `scripts/cluster-up.sh` brings everything up from scratch with a single command.

## Prerequisites
- **Phase 0 complete** — `providers/base.py`, `providers/gitlab/`, `providers/registry.py`, `providers/auth_base.py`, `providers/auth_registry.py`, and `shared/models.py` must all be merged and passing tests.
- **Local tooling installed:** `kind` (v0.23+), `kubectl`, `docker`, `helm` (v3+)
- **Resources:** GitLab CE is memory-hungry. The host machine needs at least **8 GB RAM** available to Docker, and the KIND cluster should be allowed at least 6 GB. Adjust Docker Desktop memory limits accordingly.

---

## Networking Design

All services are accessed via the nginx ingress controller on `localhost:8080`. Each service gets its own hostname under `.localhost`, which resolves to `127.0.0.1` without any `/etc/hosts` edits on most systems (Linux and macOS with recent browsers). Windows users may need to add entries manually.

| Service | URL | Notes |
|---|---|---|
| GitLab CE | `http://gitlab.localhost:8080` | Full GitLab web UI + API + webhook sender |
| Phalanx gateway | `http://phalanx.localhost:8080` | Gateway API and dashboard |
| Local registry | `localhost:5001` | Docker registry; reachable from host and cluster nodes |

Because both GitLab and the gateway are **inside the same cluster**, GitLab can reach the gateway's webhook endpoint directly via the in-cluster Service DNS name (`http://pi-agent-gateway.pi-agents.svc.cluster.local`), with no tunnel required. This is the URL registered as the GitLab webhook.

---

## KIND Cluster Setup

### `kind/cluster-config.yaml`
Three-node cluster: one control-plane (ingress-ready) and two workers so agent Jobs schedule independently from the gateway and GitLab pods.

```yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
name: pi-agent
nodes:
  - role: control-plane
    kubeadmConfigPatches:
      - |
        kind: InitConfiguration
        nodeRegistration:
          kubeletExtraArgs:
            node-labels: "ingress-ready=true"
    extraPortMappings:
      - containerPort: 80
        hostPort: 8080
        protocol: TCP
      - containerPort: 443
        hostPort: 8443
        protocol: TCP
  - role: worker
  - role: worker
```

### `kind/registry-configmap.yaml`
Tells KIND nodes where to find the local Docker registry:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "localhost:5001"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
```

---

## GitLab CE Deployment

GitLab CE is deployed into a dedicated `gitlab` namespace using the official Helm chart, with a configuration profile that is minimal but fully functional — SSH disabled, CI runners disabled, Prometheus disabled, resource requests trimmed for local use.

### `k8s/gitlab/namespace.yaml`
```yaml
apiVersion: v1
kind: Namespace
metadata:
  name: gitlab
```

### `k8s/gitlab/helm-values.yaml`
Helm values for `gitlab/gitlab` chart tuned for a KIND environment:

```yaml
global:
  hosts:
    domain: localhost
    externalIP: 127.0.0.1
    https: false
    gitlab:
      name: gitlab.localhost
      https: false
  ingress:
    class: nginx
    annotations:
      nginx.ingress.kubernetes.io/proxy-body-size: "0"
    tls:
      enabled: false
  # Use a fixed initial root password via a pre-created secret
  initialRootPassword:
    secret: gitlab-root-password
    key: password

# Disable components not needed for local dev
gitlab-runner:
  install: false
registry:
  enabled: false
prometheus:
  install: false
grafana:
  enabled: false
certmanager:
  install: false
nginx-ingress:
  enabled: false   # we use the KIND nginx ingress instead
gitlab-zoekt:
  install: false

# Trim resource requests so GitLab fits in a local cluster
gitlab:
  webservice:
    minReplicas: 1
    maxReplicas: 1
    resources:
      requests:
        cpu: 300m
        memory: 1.5Gi
  sidekiq:
    resources:
      requests:
        cpu: 100m
        memory: 512Mi
  gitaly:
    resources:
      requests:
        cpu: 100m
        memory: 200Mi
  gitlab-shell:
    enabled: false   # no SSH needed

postgresql:
  resources:
    requests:
      cpu: 100m
      memory: 256Mi

redis:
  resources:
    requests:
      cpu: 50m
      memory: 64Mi

minio:
  resources:
    requests:
      cpu: 50m
      memory: 128Mi
```

### `k8s/gitlab/root-password-secret.yaml`
Pre-create the root password secret before installing the chart. The password is used by the seed script (see below) and should not be committed with a real value — the file contains a placeholder:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: gitlab-root-password
  namespace: gitlab
type: Opaque
stringData:
  password: "changeme-local-only"   # override via scripts/cluster-up.sh
```

### `k8s/gitlab/ingress.yaml`
Route `gitlab.localhost` through the nginx ingress to the GitLab webservice:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: gitlab
  namespace: gitlab
  annotations:
    nginx.ingress.kubernetes.io/proxy-body-size: "0"
    nginx.ingress.kubernetes.io/proxy-read-timeout: "600"
spec:
  ingressClassName: nginx
  rules:
    - host: gitlab.localhost
      http:
        paths:
          - path: /
            pathType: Prefix
            backend:
              service:
                name: gitlab-webservice-default
                port:
                  number: 8080
```

---

## GitLab Seed Script

After GitLab starts, a seed script uses the GitLab API to create all the resources the pi-agent needs for testing. This makes E2E tests fully reproducible — no manual GitLab configuration required.

### `scripts/seed-gitlab.sh`
```bash
#!/usr/bin/env bash
# Seed the in-cluster GitLab instance with a test user, group, project,
# access token, and webhook pointing at the pi-agent gateway.
set -euo pipefail

GITLAB_URL="http://gitlab.localhost:8080"
ROOT_PASSWORD="${GITLAB_ROOT_PASSWORD:-changeme-local-only}"
WEBHOOK_SECRET="${GITLAB_WEBHOOK_SECRET:-dev-webhook-secret}"

echo "⏳ Waiting for GitLab to become ready..."
until curl -sf "${GITLAB_URL}/-/readiness" > /dev/null 2>&1; do
  sleep 5
done
echo "✅ GitLab is up"

# ── Get a root API token ───────────────────────────────────────────────────────
ROOT_TOKEN=$(curl -sf --request POST "${GITLAB_URL}/oauth/token" \
  --data "grant_type=password&username=root&password=${ROOT_PASSWORD}" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

AUTH="Authorization: Bearer ${ROOT_TOKEN}"

# ── Create a test group ────────────────────────────────────────────────────────
GROUP_ID=$(curl -sf --request POST "${GITLAB_URL}/api/v4/groups" \
  --header "${AUTH}" \
  --data "name=pi-agent-test&path=pi-agent-test&visibility=private" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "✅ Created group pi-agent-test (id=${GROUP_ID})"

# ── Create a test project inside the group ─────────────────────────────────────
PROJECT_ID=$(curl -sf --request POST "${GITLAB_URL}/api/v4/projects" \
  --header "${AUTH}" \
  --data "name=test-repo&namespace_id=${GROUP_ID}&initialize_with_readme=true&visibility=private" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "✅ Created project test-repo (id=${PROJECT_ID})"

# ── Create a project access token for the pi-agent service account ─────────────
SERVICE_TOKEN=$(curl -sf --request POST \
  "${GITLAB_URL}/api/v4/projects/${PROJECT_ID}/access_tokens" \
  --header "${AUTH}" \
  --data "name=pi-agent&scopes[]=api&access_level=40&expires_at=2099-01-01" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")
echo "✅ Created project access token"

# ── Register the webhook pointing at the in-cluster gateway ───────────────────
# Gateway is reachable from within the cluster via Service DNS.
# We register the ingress URL so it also works from the host.
WEBHOOK_URL="http://phalanx.localhost:8080/webhook/gitlab"
curl -sf --request POST \
  "${GITLAB_URL}/api/v4/projects/${PROJECT_ID}/hooks" \
  --header "${AUTH}" \
  --data "url=${WEBHOOK_URL}" \
  --data "token=${WEBHOOK_SECRET}" \
  --data "push_events=true" \
  --data "merge_requests_events=true" \
  --data "note_events=true" \
  > /dev/null
echo "✅ Registered webhook → ${WEBHOOK_URL}"

# ── Write credentials into the pi-agent K8s secrets ──────────────────────────
kubectl create secret generic gitlab-creds \
  --namespace pi-agents \
  --from-literal=token="${SERVICE_TOKEN}" \
  --from-literal=webhook-secret="${WEBHOOK_SECRET}" \
  --dry-run=client -o yaml | kubectl apply -f -
echo "✅ Updated gitlab-creds secret in pi-agents namespace"

# ── Write a summary env file for use in tests ─────────────────────────────────
cat > .env.test <<EOF
GITLAB_URL=http://gitlab.localhost:8080
GITLAB_TOKEN=${SERVICE_TOKEN}
GITLAB_WEBHOOK_SECRET=${WEBHOOK_SECRET}
GITLAB_PROJECT_ID=${PROJECT_ID}
GITLAB_PROJECT_PATH=pi-agent-test/test-repo
EOF
echo "✅ Test credentials written to .env.test"
echo ""
echo "GitLab UI:  http://gitlab.localhost:8080  (root / ${ROOT_PASSWORD})"
echo "Phalanx:    http://phalanx.localhost:8080"
```

`.env.test` is gitignored. It is sourced by E2E tests and local dev scripts to obtain `GITLAB_TOKEN`, `GITLAB_PROJECT_ID`, etc. without hardcoding values.

---

## `scripts/cluster-up.sh`
Single script to stand up the complete environment. Idempotent — safe to re-run.

```bash
#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="pi-agent"
REGISTRY_NAME="pi-agent-registry"
REGISTRY_PORT="5001"
GITLAB_ROOT_PASSWORD="${GITLAB_ROOT_PASSWORD:-changeme-local-only}"

# ── 1. Local Docker registry ──────────────────────────────────────────────────
if ! docker ps --format '{{.Names}}' | grep -q "^${REGISTRY_NAME}$"; then
  echo "▶ Starting local registry on port ${REGISTRY_PORT}..."
  docker run -d --restart=always \
    -p "127.0.0.1:${REGISTRY_PORT}:5000" \
    --name "${REGISTRY_NAME}" registry:2
fi

# ── 2. KIND cluster ───────────────────────────────────────────────────────────
if ! kind get clusters 2>/dev/null | grep -q "^${CLUSTER_NAME}$"; then
  echo "▶ Creating KIND cluster..."
  kind create cluster --config kind/cluster-config.yaml --name "${CLUSTER_NAME}"
fi

docker network connect "kind" "${REGISTRY_NAME}" 2>/dev/null || true
kubectl apply -f kind/registry-configmap.yaml

# ── 3. nginx ingress controller ───────────────────────────────────────────────
echo "▶ Installing nginx ingress controller..."
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=120s

# ── 4. GitLab CE ──────────────────────────────────────────────────────────────
echo "▶ Deploying GitLab CE (this takes 3–5 minutes on first run)..."
helm repo add gitlab https://charts.gitlab.io/ 2>/dev/null || true
helm repo update

kubectl apply -f k8s/gitlab/namespace.yaml

# Create root password secret before Helm install
kubectl create secret generic gitlab-root-password \
  --namespace gitlab \
  --from-literal=password="${GITLAB_ROOT_PASSWORD}" \
  --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install gitlab gitlab/gitlab \
  --namespace gitlab \
  --values k8s/gitlab/helm-values.yaml \
  --timeout 10m \
  --wait

kubectl apply -f k8s/gitlab/ingress.yaml

echo "⏳ Waiting for GitLab webservice to be ready..."
kubectl wait --namespace gitlab \
  --for=condition=ready pod \
  --selector=app=webservice \
  --timeout=300s

# ── 5. Build and push pi-agent images ─────────────────────────────────────────
echo "▶ Building and pushing pi-agent images..."
docker build -t "localhost:${REGISTRY_PORT}/pi-agent-gateway:latest" -f Dockerfile.gateway .
docker push "localhost:${REGISTRY_PORT}/pi-agent-gateway:latest"

docker build -t "localhost:${REGISTRY_PORT}/pi-agent-worker:latest" -f Dockerfile.worker .
docker push "localhost:${REGISTRY_PORT}/pi-agent-worker:latest"

# ── 6. pi-agent manifests ─────────────────────────────────────────────────────
echo "▶ Applying pi-agent manifests..."
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/rbac.yaml
# Apply placeholder LLM secret (override OPENAI_API_KEY before running E2E tests)
kubectl create secret generic llm-creds \
  --namespace pi-agents \
  --from-literal=api-key="${OPENAI_API_KEY:-placeholder}" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f k8s/gateway-deployment.yaml
kubectl apply -f k8s/ingress.yaml

kubectl rollout status deployment/pi-agent-gateway -n pi-agents

# ── 7. Seed GitLab ────────────────────────────────────────────────────────────
echo "▶ Seeding GitLab with test project and webhook..."
GITLAB_ROOT_PASSWORD="${GITLAB_ROOT_PASSWORD}" bash scripts/seed-gitlab.sh

echo ""
echo "✅ Environment ready"
echo "   GitLab:   http://gitlab.localhost:8080  (root / ${GITLAB_ROOT_PASSWORD})"
echo "   Gateway:  http://phalanx.localhost:8080"
echo "   Test credentials: .env.test"
```

## `scripts/cluster-down.sh`
```bash
#!/usr/bin/env bash
kind delete cluster --name pi-agent
docker rm -f pi-agent-registry 2>/dev/null || true
rm -f .env.test
echo "✅ Cluster and registry deleted"
```

## `scripts/load-images.sh`
Rebuild and redeploy without recreating the cluster or GitLab. Use this during active development after gateway or worker code changes:

```bash
#!/usr/bin/env bash
set -euo pipefail
REGISTRY_PORT="5001"

docker build -t "localhost:${REGISTRY_PORT}/pi-agent-gateway:latest" -f Dockerfile.gateway .
docker push "localhost:${REGISTRY_PORT}/pi-agent-gateway:latest"

docker build -t "localhost:${REGISTRY_PORT}/pi-agent-worker:latest" -f Dockerfile.worker .
docker push "localhost:${REGISTRY_PORT}/pi-agent-worker:latest"

kubectl rollout restart deployment/pi-agent-gateway -n pi-agents
kubectl rollout status deployment/pi-agent-gateway -n pi-agents
echo "✅ Images reloaded"
```

## `scripts/reseed-gitlab.sh`
Re-run only the GitLab seed (useful if the cluster is up but credentials need refreshing):

```bash
#!/usr/bin/env bash
GITLAB_ROOT_PASSWORD="${GITLAB_ROOT_PASSWORD:-changeme-local-only}" bash scripts/seed-gitlab.sh
```

---

## Application Deliverables

### `gateway/db.py` (jobs table only)
Async SQLite store using `aiosqlite`. Implement only the `jobs` table at this phase (log events come in Phase 3, sessions in Phase 7).

Required methods:
- `create_job(job: JobRecord)` — insert a new row
- `update_job_status(job_id: str, status: str, finished_at: datetime | None = None)` — update status and optional finish time
- `get_job(job_id: str) → JobRecord` — fetch by ID; raise if not found
- `list_jobs(status: list[str] | None, limit: int, offset: int) → list[JobRecord]` — filtered list with pagination

### `gateway/kube_client.py` (basic spawning)
Spawn ephemeral K8s Jobs. At this phase, use the global worker image unconditionally — `AgentConfig` resolution comes in Phase 4.

```python
class KubeClient:
    def __init__(self): ...  # load_incluster_config, fall back to load_kube_config
    def spawn_agent_job(self, task_spec: TaskSpec) -> str: ...  # returns job_name
```

Job manifest requirements:
- `restart_policy: Never`
- `ttlSecondsAfterFinished: 300`
- `serviceAccountName: pi-agent-worker`
- Env vars: `TASK`, `PROJECT_ID`, `TASK_CONTEXT` (JSON), `GITLAB_TOKEN` (from secret), `OPENAI_API_KEY` (from secret), `LLM_ENDPOINT`, `GITLAB_URL` (from configmap — points at in-cluster GitLab)
- Image from `PI_AGENT_IMAGE` env var

### `gateway/event_mapper.py`
Maps provider-agnostic event models to `TaskSpec`. No provider-specific code.

```python
def map_event_to_task(event: PushEvent | MREvent | CommentEvent) -> TaskSpec | None
```

Mapping:
- `MREvent` → `TaskSpec(task="review_mr", ...)`
- `CommentEvent` → `TaskSpec(task="handle_comment", ...)`
- `PushEvent` → `TaskSpec(task="analyze_push", ...)`
- Unrecognised/None → `None`

All context dict field names must exactly match the keys used in `agent_runner.py`'s `build_task_message`.

### `gateway/main.py` (Phase 1 endpoints)

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/gitlab` | Verify HMAC, parse event, map to TaskSpec, spawn job, create DB record |
| `POST` | `/trigger` | Accept `TaskSpec` body, spawn job, create DB record |
| `GET` | `/healthz` | Return `{"status": "ok"}` |
| `GET` | `/agents` | Return pending/running jobs from DB |
| `GET` | `/agents/history` | Return completed/failed/cancelled jobs; support `limit`/`offset` query params |
| `GET` | `/internal/oauth2-proxy-config` | Return `auth_provider.oauth_proxy_config()` as CLI args |

Webhook endpoint steps:
1. Call `provider.verify_webhook(headers, body, secret)` — return 401 on failure
2. Call `provider.parse_webhook_event(headers, body)`
3. Call `map_event_to_task(event)` — return 200 with no body if `None`
4. Call `config_loader.resolve(project_id, sha)` to get `AgentConfig` (Phase 4 wires this; at Phase 1 use defaults)
5. Check `event.actor in agent_config.allowed_users` — return 200 with no body if actor not permitted
6. Call `kube_client.spawn_agent_job(task_spec)`
7. Call `db.create_job(...)` with status `"pending"`
8. Return `{"job_name": job_name}`

### `k8s/` manifests

- `k8s/namespace.yaml` — `pi-agents` namespace
- `k8s/gateway-deployment.yaml` — gateway Deployment (2 replicas) + Service
  - Image: `localhost:5001/pi-agent-gateway:latest`
  - `imagePullPolicy: Always`
  - `PI_AGENT_IMAGE`: `localhost:5001/pi-agent-worker:latest`
  - `GITLAB_URL`: `http://gitlab-webservice-default.gitlab.svc.cluster.local:8080` (in-cluster DNS — used by the provider client inside the gateway and worker pods)
- `k8s/rbac.yaml` — `pi-agent-gateway` ServiceAccount + `job-spawner` Role + RoleBinding; `pi-agent-worker` ServiceAccount
- `k8s/secrets.yaml` — `llm-creds` placeholder only; `gitlab-creds` is written by `seed-gitlab.sh` at setup time and should not be committed
- `k8s/ingress.yaml` — nginx ingress; `phalanx.localhost` → gateway Service; `gitlab.localhost` → GitLab webservice (can be a single Ingress resource with two rules)

### `Dockerfile.gateway`
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway/ ./gateway/
COPY shared/ ./shared/
COPY providers/ ./providers/
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "3000"]
```

### `docs/local-development.md`
Document the full local dev workflow:
- Prerequisites and resource requirements
- `scripts/cluster-up.sh` first-run walkthrough
- How to rebuild images with `scripts/load-images.sh`
- How `.env.test` is used by tests
- How to access GitLab UI and the gateway
- How to re-seed GitLab after a cluster wipe
- Note that `OPENAI_API_KEY` must be set in the environment before `cluster-up.sh` for E2E tests that call the LLM

---

## Tests to Write First (TDD)

### Unit tests — `gateway/event_mapper.py`
- `map_event_to_task` correctly maps `MREvent` → `TaskSpec(task="review_mr")` with correct context fields
- `map_event_to_task` correctly maps `CommentEvent` → `TaskSpec(task="handle_comment")`
- `map_event_to_task` correctly maps `PushEvent` → `TaskSpec(task="analyze_push")`
- `map_event_to_task` returns `None` for `None` input
- `map_event_to_task` handles `CommentEvent` with no `mr_iid` gracefully

### Unit tests — `gateway/db.py`
- `create_job`, `update_job_status`, `list_jobs`, `get_job` round-trip correctly
- `list_jobs` with `status` filter returns only matching records
- `list_jobs` respects `limit` and `offset`
- `get_job` raises on unknown job ID

### Unit tests — `gateway/kube_client.py`
- Mock the K8s batch 
