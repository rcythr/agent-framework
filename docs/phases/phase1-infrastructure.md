# Phase 1 — Infrastructure Foundation

## Goal
A working Kubernetes namespace running on a local [KIND](https://kind.sigs.k8s.io/) (Kubernetes IN Docker) cluster, with the gateway deployed and able to receive webhook events and spawn K8s Jobs. The system must persist job records and return them via API.

KIND is used as the target environment for all local development, integration testing, and E2E testing. The same K8s manifests apply to a production cluster — KIND is purely a local runtime convenience that requires no cloud account.

## Prerequisites
- **Phase 0 complete** — `providers/base.py`, `providers/gitlab/`, `providers/registry.py`, `providers/auth_base.py`, `providers/auth_registry.py`, and `shared/models.py` must all be merged and passing tests.
- **Local tooling installed:** `kind` (v0.23+), `kubectl`, `docker`, `helm` (optional but recommended for ingress)

---

---

## KIND Cluster Setup

### `kind/cluster-config.yaml`
Define the KIND cluster with port mappings so the gateway is reachable from the host (for webhooks via a tunnel) and the ingress controller can bind to host ports 80/443:

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
        hostPort: 8080        # http://localhost:8080 reaches the ingress
        protocol: TCP
      - containerPort: 443
        hostPort: 8443
        protocol: TCP
  - role: worker
  - role: worker
```

Two worker nodes are included so agent Jobs can schedule independently of the gateway pod.

### `scripts/cluster-up.sh`
Single script to create the cluster, load images, install the ingress controller, and apply all manifests. Agents running the codebase should be able to bring up a fully working environment with one command.

```bash
#!/usr/bin/env bash
set -euo pipefail

CLUSTER_NAME="pi-agent"
REGISTRY_NAME="pi-agent-registry"
REGISTRY_PORT="5001"

# ── 1. Local registry ────────────────────────────────────────────────────────
# KIND cannot pull from a remote registry during offline dev, so we run a
# local Docker registry and connect it to the KIND network.
if ! docker ps | grep -q "${REGISTRY_NAME}"; then
  docker run -d --restart=always -p "127.0.0.1:${REGISTRY_PORT}:5000" \
    --network bridge --name "${REGISTRY_NAME}" registry:2
fi

# ── 2. Create cluster ─────────────────────────────────────────────────────────
if ! kind get clusters | grep -q "^${CLUSTER_NAME}$"; then
  kind create cluster --config kind/cluster-config.yaml --name "${CLUSTER_NAME}"
fi

# Connect local registry to the KIND Docker network
docker network connect "kind" "${REGISTRY_NAME}" 2>/dev/null || true

# Patch CoreDNS / registry configmap so KIND nodes can resolve the registry
kubectl apply -f kind/registry-configmap.yaml

# ── 3. Ingress controller (nginx) ─────────────────────────────────────────────
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml
kubectl wait --namespace ingress-nginx \
  --for=condition=ready pod \
  --selector=app.kubernetes.io/component=controller \
  --timeout=90s

# ── 4. Build and push images ──────────────────────────────────────────────────
docker build -t "localhost:${REGISTRY_PORT}/pi-agent-gateway:latest" -f Dockerfile.gateway .
docker push "localhost:${REGISTRY_PORT}/pi-agent-gateway:latest"

docker build -t "localhost:${REGISTRY_PORT}/pi-agent-worker:latest" -f Dockerfile.worker .
docker push "localhost:${REGISTRY_PORT}/pi-agent-worker:latest"

# ── 5. Apply manifests ────────────────────────────────────────────────────────
kubectl apply -f k8s/namespace.yaml
kubectl apply -f k8s/secrets.yaml       # contains placeholder values; override before use
kubectl apply -f k8s/rbac.yaml
kubectl apply -f k8s/gateway-deployment.yaml
kubectl apply -f k8s/ingress.yaml

kubectl rollout status deployment/pi-agent-gateway -n pi-agents

echo "✅ Cluster ready. Gateway at http://localhost:8080"
```

### `scripts/cluster-down.sh`
```bash
#!/usr/bin/env bash
kind delete cluster --name pi-agent
docker rm -f pi-agent-registry 2>/dev/null || true
echo "✅ Cluster deleted"
```

### `scripts/load-images.sh`
Re-build and push images without recreating the cluster. Use this during active development after code changes:

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

### `kind/registry-configmap.yaml`
Tells KIND nodes where to find the local registry:

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

### Webhook tunnelling for local GitLab integration
KIND runs entirely inside Docker, so GitLab cannot reach `localhost:8080` directly. Use [ngrok](https://ngrok.com/) or [smee.io](https://smee.io/) to create a public tunnel during local development:

```bash
# Option A — ngrok (requires free account)
ngrok http 8080
# Copy the https://xxxxx.ngrok.io URL into your GitLab webhook settings

# Option B — smee.io (no account needed)
npx smee-client --url https://smee.io/<your-channel> --target http://localhost:8080/webhook/gitlab
```

Document this in `docs/local-development.md`. The gateway webhook endpoint does not need to change — only the URL registered in GitLab differs between local and production.

---

## Deliverables

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
- Env vars: `TASK`, `PROJECT_ID`, `TASK_CONTEXT` (JSON), `GITLAB_TOKEN` (from secret), `OPENAI_API_KEY` (from secret), `LLM_ENDPOINT`
- Image from `PI_AGENT_IMAGE` env var

### `gateway/event_mapper.py`
Maps provider-agnostic event models to `TaskSpec`. No provider-specific code — the provider's `parse_webhook_event` has already translated the raw payload before this is called.

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
FastAPI app with the following endpoints:

| Method | Path | Description |
|---|---|---|
| `POST` | `/webhook/gitlab` | Verify HMAC, parse event, map to TaskSpec, spawn job, create DB record |
| `POST` | `/trigger` | Accept `TaskSpec` body, spawn job, create DB record |
| `GET` | `/healthz` | Return `{"status": "ok"}` |
| `GET` | `/agents` | Return pending/running jobs from DB |
| `GET` | `/agents/history` | Return completed/failed/cancelled jobs; support `limit`/`offset` query params |
| `GET` | `/internal/oauth2-proxy-config` | Return `auth_provider.oauth_proxy_config()` as CLI args |

Webhook endpoint must:
1. Call `provider.verify_webhook(headers, body, secret)` — return 401 on failure
2. Call `provider.parse_webhook_event(headers, body)`
3. Call `map_event_to_task(event)` — return 200 with no body if `None` (unhandled event type)
4. Call `kube_client.spawn_agent_job(task_spec)` 
5. Call `db.create_job(...)` with status `"pending"`
6. Return `{"job_name": job_name}`

### `k8s/` manifests
- `k8s/namespace.yaml` — `pi-agents` namespace
- `k8s/gateway-deployment.yaml` — gateway Deployment (2 replicas) + Service; image set to `localhost:5001/pi-agent-gateway:latest`; `imagePullPolicy: Always` so the cluster picks up newly pushed images on rollout restart
- `k8s/rbac.yaml` — `pi-agent-gateway` ServiceAccount + `job-spawner` Role + RoleBinding; `pi-agent-worker` ServiceAccount
- `k8s/secrets.yaml` — `gitlab-creds` (token, webhook-secret) and `llm-creds` (api-key) with placeholder base64 values and apply instructions
- `k8s/ingress.yaml` — nginx ingress; host `pi-agent.localhost` (resolves to `127.0.0.1` without any `/etc/hosts` changes on most systems); no oauth2-proxy routing yet (added in Phase 5)

Update `PI_AGENT_IMAGE` env var in `gateway-deployment.yaml` to `localhost:5001/pi-agent-worker:latest` so the job spawner uses the local registry image for worker pods.

### `Dockerfile.gateway`
```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY gateway/ ./gateway/
COPY shared/ ./shared/
CMD ["uvicorn", "gateway.main:app", "--host", "0.0.0.0", "--port", "3000"]
```

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
- Mock the K8s batch API; assert correct Job manifest shape: env vars present, correct image, `restart_policy=Never`, `ttlSecondsAfterFinished=300`
- Assert `GITLAB_TOKEN` is sourced from secret ref, not plain env value
- Assert `OPENAI_API_KEY` is sourced from secret ref

### Unit tests — `providers/gitlab/auth.py`
- `GitLabAuthProvider.extract_user()` reads `X-Forwarded-User`, `X-Forwarded-Email`, `X-Forwarded-Groups` and returns `UserIdentity`
- `GitLabAuthProvider.oauth_proxy_config()` returns `provider_flag="gitlab"` and `--gitlab-group` in `extra_flags`

### Unit tests — `gateway/main.py`
- `GET /internal/oauth2-proxy-config` renders `auth_provider.oauth_proxy_config()` as CLI args correctly

### Integration tests
- `POST /webhook/gitlab` with valid token and MR Hook payload returns `{"job_name": ...}` and creates a DB record with status `"pending"`
- `POST /webhook/gitlab` with invalid token returns 401
- `POST /webhook/gitlab` with unhandled event type returns 200 with no job created
- `POST /trigger` with valid `TaskSpec` body spawns a job and persists a `JobRecord`
- `GET /agents` returns only jobs with status `pending` or `running`
- `GET /agents/history` returns completed/failed/cancelled jobs with correct pagination

### E2E test (KIND cluster)
- Run `scripts/cluster-up.sh` to bring up a fresh KIND cluster
- Apply secrets with real test values (`GITLAB_TOKEN`, `GITLAB_WEBHOOK_SECRET`)
- Start ngrok or smee tunnel; register the public URL as a GitLab webhook
- Post a real GitLab MR webhook (or open/update an MR in the test project)
- Verify a K8s Job appears: `kubectl get jobs -n pi-agents`
- Verify a DB record is created and returned by `GET http://localhost:8080/agents`

---

## Definition of Done
Gateway runs in a local KIND cluster, receives a real GitLab MR webhook (via ngrok/smee tunnel), spawns a K8s Job, and the Job record is visible via `GET http://localhost:8080/agents`. `scripts/cluster-up.sh` brings up a fully working environment from scratch in a single command.

## Dependencies
- **Blocked by:** Phase 0 (provider abstraction, shared models)
