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
