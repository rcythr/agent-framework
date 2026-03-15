# Provider setup — GitLab

This document covers everything needed to connect Phalanx to a GitLab instance, whether GitLab.com or self-hosted GitLab CE/EE.

---

## Overview

| Item | Value |
|---|---|
| `PROVIDER` env var | `gitlab` |
| Credential type | Personal access token (or service account token) |
| Project ID format | Integer (e.g. `42`) or `namespace/project` slug |
| Webhook signature | `X-Gitlab-Token` header — direct string comparison (not HMAC) |
| Webhook events | Push, Merge Request, Note (comments), Confidential Note |
| oauth2-proxy provider | `gitlab` |

---

## Step 1 — Create a service account token

Phalanx needs a GitLab token with enough permission to read files, open merge requests, and post comments. You can use a personal access token for development; a dedicated service account or project access token is better for production.

**Scopes required:**

| Scope | Why |
|---|---|
| `api` | Read/write files, create MRs, post comments, register webhooks |

**Steps (GitLab.com or self-hosted):**

1. Go to **User Settings → Access Tokens → Add new token** (or use a group/project access token under **Group/Project → Settings → Access Tokens**).
2. Give it a name (e.g. `phalanx`).
3. Select scope: `api`.
4. Click **Create personal access token** and copy the value — you will not see it again.

Set this as `GITLAB_TOKEN` in the gateway environment (or `gitlab.token` in Helm values).

For self-hosted instances also set `GITLAB_URL` to the root URL of your instance, e.g. `https://gitlab.example.com`. For GitLab.com this is not required (the default is `https://gitlab.com`).

---

## Step 2 — Create a GitLab OAuth2 application (for dashboard login)

The dashboard is protected by oauth2-proxy using GitLab as the OAuth/OIDC identity provider.

1. Go to **User Settings → Applications → Add new application** (or a Group-level application under **Group → Settings → Applications**).
2. Fill in:
   - **Name:** `phalanx`
   - **Redirect URI:** `https://phalanx.example.com/oauth2/callback` (use `http://phalanx.localhost:8080/oauth2/callback` for local KIND dev)
   - **Scopes:** `api`, `read_user`, `openid`

   > The `api` scope is required so that oauth2-proxy can forward a usable access token via `X-Forwarded-Access-Token`. The gateway uses this for user-scoped project search and webhook registration — without it, those features silently fail.

3. Click **Save application** and copy the **Application ID** and **Secret**.

---

## Step 3 — Configure a webhook on your project

For each GitLab project you want Phalanx to watch:

1. Go to **Settings → Webhooks → Add new webhook**.
2. Set **URL** to `https://phalanx.example.com/webhook/gitlab`.
3. Set **Secret token** to the same random value you will use for `GITLAB_WEBHOOK_SECRET`.
4. Enable these triggers:
   - **Push events**
   - **Merge request events**
   - **Comments**
   - **Confidential comments** (optional but recommended)
5. Click **Add webhook**.

**Generate a webhook secret:**

```bash
openssl rand -hex 32
```

Unlike GitHub and Bitbucket, GitLab webhook verification is a direct string comparison (the `X-Gitlab-Token` header is checked against the stored secret using a constant-time compare). There is no HMAC involved.

---

## Step 4 — Add `.agents/config.yaml` to your project

Without this file no automatic dispatch occurs (deny-by-default):

```yaml
# .agents/config.yaml
allowed_users:
  - your-gitlab-username
  - another-team-member

gas_limit_input: 80000
gas_limit_output: 20000

prompt_mode: append
prompt: |
  This project uses Python 3.12 and follows PEP 8.
```

Commit this to the branch you want Phalanx to watch (usually `main`).

---

## Step 5 — Deploy

### Helm

```bash
helm install phalanx ./helm/phalanx \
  --namespace pi-agents --create-namespace \
  --set provider=gitlab \
  --set gitlab.url=https://gitlab.example.com \
  --set gitlab.token=glpat-xxxxxxxxxxxx \
  --set gitlab.webhookSecret=$(openssl rand -hex 32) \
  --set gitlab.group=my-gitlab-group \
  --set llm.apiKey=sk-... \
  --set llm.baseUrl=https://api.openai.com/v1 \
  --set llm.model=gpt-4o \
  --set oauth2proxy.clientId=<application-id> \
  --set oauth2proxy.clientSecret=<application-secret> \
  --set oauth2proxy.cookieSecret=$(openssl rand -base64 32) \
  --set ingress.host=phalanx.example.com
```

`gitlab.group` is optional — when set, oauth2-proxy restricts dashboard login to members of that group.

### Raw Kubernetes manifests (local dev)

Edit `k8s/secrets.yaml` to fill in `gitlab-creds`, then:

```bash
kubectl apply -f k8s/
```

Or use the automated script which does all of this:

```bash
export OPENAI_API_KEY=sk-...
./scripts/cluster-up.sh
```

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `PROVIDER` | Yes | Set to `gitlab` |
| `GITLAB_TOKEN` | Yes | Personal/service account access token with `api` scope |
| `GITLAB_URL` | Self-hosted only | Root URL of the GitLab instance (default: `https://gitlab.com`) |
| `GITLAB_WEBHOOK_SECRET` | Yes | Shared secret for webhook signature verification |

---

## Troubleshooting

**Webhooks are delivered but no job appears**
- Check `allowed_users` in `.agents/config.yaml`. The username in the payload must match exactly (case-sensitive).
- Check gateway logs: `kubectl logs -n pi-agents -l app=pi-agent-gateway`.
- In GitLab, go to **Settings → Webhooks → Recent deliveries** and check the response body.

**"401 Unauthorized" in gateway logs**
- The `GITLAB_TOKEN` may have expired or have insufficient scope. It needs `api`.

**Project search returns no results**
- Confirm the GitLab OAuth app has the `api` scope and oauth2-proxy is configured with `--pass-access-token=true`. The dashboard uses the forwarded user token for project search.

**Self-hosted: TLS errors**
- If your GitLab instance uses a certificate from a private CA, follow the [custom root certificate guide](../walkthrough.md#part-5--custom-root-certificates-enterprise-environments).
