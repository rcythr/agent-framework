# Provider setup — GitHub

This document covers everything needed to connect Phalanx to GitHub.com. GitHub Enterprise Server is supported by pointing `GITHUB_URL` at your instance (see below).

---

## Overview

| Item | Value |
|---|---|
| `PROVIDER` env var | `github` |
| Credential type | Personal access token (classic) or fine-grained PAT |
| Project ID format | `owner/repo` (e.g. `acme/my-service`) |
| Webhook signature | `X-Hub-Signature-256` header — HMAC-SHA256, prefixed `sha256=` |
| Webhook events | `push`, `pull_request`, `issue_comment`, `pull_request_review_comment` |
| oauth2-proxy provider | `github` |

---

## Step 1 — Create a personal access token

Phalanx needs a GitHub token to read files, open pull requests, post comments, and register webhooks.

**Option A — Classic PAT (simpler)**

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token (classic)**.
2. Give it a descriptive name (e.g. `phalanx-bot`).
3. Select scopes:
   - `repo` — full repository access (read files, write files, open PRs, post comments)
   - `admin:repo_hook` — register and manage webhooks
4. Click **Generate token** and copy the value.

**Option B — Fine-grained PAT**

1. Go to **GitHub → Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token**.
2. Set the resource owner to the org or account that owns the repositories.
3. Under **Repository permissions**, grant:
   - **Contents**: Read and write
   - **Pull requests**: Read and write
   - **Commit statuses**: Read and write
   - **Webhooks**: Read and write
4. Generate and copy the token.

Set this as `GITHUB_TOKEN` in the gateway environment (or `github.token` in Helm values).

---

## Step 2 — Create a GitHub OAuth App (for dashboard login)

The dashboard is protected by oauth2-proxy using GitHub as the OAuth identity provider.

1. Go to **GitHub → Settings → Developer settings → OAuth Apps → New OAuth App** (or use an Organisation OAuth App under **Org → Settings → Developer settings → OAuth Apps**).
2. Fill in:
   - **Application name:** `phalanx`
   - **Homepage URL:** `https://phalanx.example.com`
   - **Authorization callback URL:** `https://phalanx.example.com/oauth2/callback`
3. Click **Register application**, then generate a **Client secret**.

> oauth2-proxy forwards the GitHub access token as `X-Forwarded-Access-Token` (with `--pass-access-token=true`). The gateway uses this for user-scoped project search. The default GitHub OAuth scopes (`read:user`, `user:email`) are sufficient for authentication; the `repo` scope on the service token covers API operations.

Copy the **Client ID** and **Client Secret** for use in the next step.

---

## Step 3 — Configure a webhook on your repository

For each GitHub repository you want Phalanx to watch:

1. Go to **Repository → Settings → Webhooks → Add webhook**.
2. Set **Payload URL** to `https://phalanx.example.com/webhook/github`.
3. Set **Content type** to `application/json`.
4. Set **Secret** to the same random value you will use for `GITHUB_WEBHOOK_SECRET`.
5. Choose **Let me select individual events** and enable:
   - **Pushes**
   - **Pull requests**
   - **Issue comments**
   - **Pull request review comments**
6. Click **Add webhook**.

**Generate a webhook secret:**

```bash
openssl rand -hex 32
```

GitHub signs each delivery with HMAC-SHA256 using this secret and sends the signature in the `X-Hub-Signature-256` header as `sha256=<hex>`. Phalanx verifies this before processing any event.

---

## Step 4 — Add `.agents/config.yaml` to your repository

Without this file no automatic dispatch occurs (deny-by-default):

```yaml
# .agents/config.yaml
allowed_users:
  - your-github-username
  - another-team-member

gas_limit_input: 80000
gas_limit_output: 20000

prompt_mode: append
prompt: |
  This project uses Node.js 20 and follows the Airbnb style guide.
```

Commit this to the default branch (usually `main`).

---

## Step 5 — Deploy

### Helm

```bash
helm install phalanx ./helm/phalanx \
  --namespace pi-agents --create-namespace \
  --set provider=github \
  --set github.token=ghp_xxxxxxxxxxxx \
  --set github.webhookSecret=$(openssl rand -hex 32) \
  --set llm.apiKey=sk-... \
  --set llm.baseUrl=https://api.openai.com/v1 \
  --set llm.model=gpt-4o \
  --set oauth2proxy.provider=github \
  --set oauth2proxy.clientId=<oauth-app-client-id> \
  --set oauth2proxy.clientSecret=<oauth-app-client-secret> \
  --set oauth2proxy.cookieSecret=$(openssl rand -base64 32) \
  --set ingress.host=phalanx.example.com
```

To restrict dashboard access to members of a GitHub organisation, add:

```bash
  --set oauth2proxy.extraArgs[0]="--github-org=my-org"
```

### Raw Kubernetes manifests

Edit `k8s/secrets.yaml` to fill in `github-creds`, set `PROVIDER=github` in `k8s/gateway-deployment.yaml`, then:

```bash
kubectl apply -f k8s/
```

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `PROVIDER` | Yes | Set to `github` |
| `GITHUB_TOKEN` | Yes | Personal access token with `repo` and `admin:repo_hook` scopes |
| `GITHUB_WEBHOOK_SECRET` | Yes | Shared secret for HMAC-SHA256 webhook signature verification |

---

## Troubleshooting

**Webhooks are delivered but no job appears**
- Check `allowed_users` in `.agents/config.yaml`. GitHub usernames are case-sensitive.
- Check gateway logs: `kubectl logs -n pi-agents -l app=pi-agent-gateway`.
- In GitHub, go to **Repository → Settings → Webhooks → Recent Deliveries** and inspect the response.

**"Bad credentials" or 401 errors**
- The PAT may have expired (fine-grained PATs have configurable expiry). Regenerate and update the `github-creds` secret.
- Confirm the token has the `repo` scope.

**Pull request comments not appearing**
- Issue comments on PRs are delivered via the `issue_comment` event. Ensure this event is enabled on the webhook — not just `pull_request`.

**Project search returns no results in the dashboard**
- Confirm oauth2-proxy is configured with `--pass-access-token=true`. The gateway uses `X-Forwarded-Access-Token` for project search.
