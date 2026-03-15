# Provider setup — Bitbucket Cloud

This document covers everything needed to connect Phalanx to Bitbucket Cloud. Note that Bitbucket uses **app passwords** rather than API tokens, and dashboard authentication uses **Atlassian OIDC** rather than a native Bitbucket OAuth flow.

---

## Overview

| Item | Value |
|---|---|
| `PROVIDER` env var | `bitbucket` |
| Credential type | Workspace username + **app password** (not a token) |
| Project ID format | `workspace/repo_slug` (e.g. `acme/my-service`) |
| Webhook signature | `X-Hub-Signature` header — HMAC-SHA256, prefixed `sha256=` |
| Webhook events | `repo:push`, `pullrequest:created`, `pullrequest:updated`, `pullrequest:comment_created` |
| oauth2-proxy provider | `oidc` (Atlassian Identity) |

---

## Step 1 — Create a Bitbucket app password

Bitbucket does not use personal access tokens for API authentication. Instead, it uses **app passwords** — account-scoped credentials separate from your login password.

1. Go to **Bitbucket → Personal Settings → App passwords → Create app password** (URL: `https://bitbucket.org/account/settings/app-passwords/new`).
2. Give it a label (e.g. `phalanx`).
3. Grant the following permissions:

   | Category | Permission |
   |---|---|
   | Account | Read |
   | Repositories | Read, Write |
   | Pull requests | Read, Write |
   | Webhooks | Read and write |

4. Click **Create** and copy the app password — you will not see it again.

The workspace username is your Bitbucket account username (visible at `https://bitbucket.org/account/` under **Bitbucket profile settings**), not your email address.

Set these as `BITBUCKET_USERNAME` and `BITBUCKET_APP_PASSWORD` in the gateway environment.

---

## Step 2 — Create an Atlassian OAuth 2.0 app (for dashboard login)

Bitbucket Cloud uses Atlassian as its identity provider. Dashboard authentication is handled via Atlassian OIDC rather than a native Bitbucket OAuth flow. oauth2-proxy uses its generic `oidc` provider pointed at Atlassian's OIDC endpoints.

1. Go to the **Atlassian developer console**: `https://developer.atlassian.com/console/myapps/`
2. Click **Create** → **OAuth 2.0 integration**.
3. Give it a name (e.g. `phalanx`) and accept the terms.
4. Under **Permissions**, add:
   - **User identity API** — `read:me` (to read the authenticated user's profile)
5. Under **Authorization**, add a callback URL: `https://phalanx.example.com/oauth2/callback`
6. Copy the **Client ID** and **Secret** from the **Settings** tab.

**Atlassian OIDC discovery URL:**

```
https://auth.atlassian.com/.well-known/openid-configuration
```

Use this as the `--oidc-issuer-url` for oauth2-proxy (see Step 5).

> `--pass-access-token=true` is configured in the oauth2-proxy deployment. The gateway uses the forwarded Atlassian access token for user identity in project search. Because Bitbucket's API uses basic auth (not a bearer token), user-scoped project search falls back to the service account credentials — the forwarded token is used primarily for identity, not for direct API calls.

---

## Step 3 — Configure a webhook on your repository

For each Bitbucket repository you want Phalanx to watch:

1. Go to **Repository → Repository settings → Webhooks → Add webhook**.
2. Set **Title** to `phalanx`.
3. Set **URL** to `https://phalanx.example.com/webhook/bitbucket`.
4. Set **Secret** to the same random value you will use for `BITBUCKET_WEBHOOK_SECRET`.
5. Under **Triggers**, choose **Choose from a full list of triggers** and enable:
   - **Repository** → Push
   - **Pull Request** → Created, Updated, Comment created
6. Click **Save**.

**Generate a webhook secret:**

```bash
openssl rand -hex 32
```

Bitbucket signs each delivery with HMAC-SHA256 using this secret and sends the result in `X-Hub-Signature` as `sha256=<hex>` — the same format as GitHub.

---

## Step 4 — Add `.agents/config.yaml` to your repository

Without this file no automatic dispatch occurs (deny-by-default):

```yaml
# .agents/config.yaml
allowed_users:
  - your-bitbucket-username
  - another-team-member

gas_limit_input: 80000
gas_limit_output: 20000

prompt_mode: append
prompt: |
  This project uses Java 21 and follows Google Java Style.
```

Commit this to the main branch.

> **Project IDs in Bitbucket** are `workspace/repo_slug` — the slug is the URL-friendly name shown in the repository URL, e.g. `https://bitbucket.org/acme/my-service` → `acme/my-service`.

---

## Step 5 — Deploy

### Helm

Bitbucket requires overriding the oauth2-proxy provider to `oidc` and supplying the Atlassian OIDC issuer URL:

```bash
helm install phalanx ./helm/phalanx \
  --namespace pi-agents --create-namespace \
  --set provider=bitbucket \
  --set bitbucket.username=my-workspace-username \
  --set bitbucket.appPassword=<app-password> \
  --set bitbucket.webhookSecret=$(openssl rand -hex 32) \
  --set llm.apiKey=sk-... \
  --set llm.baseUrl=https://api.openai.com/v1 \
  --set llm.model=gpt-4o \
  --set oauth2proxy.provider=oidc \
  --set oauth2proxy.clientId=<atlassian-client-id> \
  --set oauth2proxy.clientSecret=<atlassian-client-secret> \
  --set oauth2proxy.cookieSecret=$(openssl rand -base64 32) \
  --set oauth2proxy.extraArgs[0]="--oidc-issuer-url=https://auth.atlassian.com" \
  --set oauth2proxy.extraArgs[1]="--scope=openid profile email read:me" \
  --set ingress.host=phalanx.example.com
```

### Raw Kubernetes manifests

Edit `k8s/secrets.yaml` to fill in `bitbucket-creds`, set `PROVIDER=bitbucket` in `k8s/gateway-deployment.yaml`, and update the oauth2-proxy args in `k8s/oauth2-proxy-deployment.yaml` to use `--provider=oidc` with the Atlassian issuer URL.

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `PROVIDER` | Yes | Set to `bitbucket` |
| `BITBUCKET_USERNAME` | Yes | Bitbucket workspace username |
| `BITBUCKET_APP_PASSWORD` | Yes | App password with repo read/write and webhook permissions |
| `BITBUCKET_WEBHOOK_SECRET` | Yes | Shared secret for HMAC-SHA256 webhook signature verification |

---

## Troubleshooting

**"Authentication failed" in gateway logs**
- Double-check `BITBUCKET_USERNAME` — it must be the account username, not an email address.
- App passwords are scoped to the creating account; if created by a team member their username must be used.
- The app password may have been revoked. Generate a new one under Personal Settings.

**Webhooks delivered but no job appears**
- Check `allowed_users`. Bitbucket sends the actor's UUID in some events; the provider resolves it to a username. If usernames don't match, check the raw payload in **Repository settings → Webhooks → View requests**.
- Ensure the `repo:push` and `pullrequest:comment_created` triggers are enabled.

**Pull request comments not created**
- Confirm the app password has **Pull requests: Write** permission.

**Dashboard login fails**
- Verify the Atlassian callback URL exactly matches what is registered in the Atlassian developer console, including the scheme (`https://`).
- The `read:me` permission must be enabled on the Atlassian OAuth app for user identity to work.
