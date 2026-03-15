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
