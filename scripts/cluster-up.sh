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
