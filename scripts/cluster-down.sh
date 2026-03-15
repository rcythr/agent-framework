#!/usr/bin/env bash
kind delete cluster --name pi-agent
docker rm -f pi-agent-registry 2>/dev/null || true
rm -f .env.test
echo "✅ Cluster and registry deleted"
