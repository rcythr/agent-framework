# Provider setup — Gitea

This document covers everything needed to connect Phalanx to a self-hosted Gitea instance. Gitea is a lightweight self-hosted Git service with a GitHub-like API, making it a popular choice in air-gapped or on-premises environments.

---

## Overview

| Item | Value |
|---|---|
| `PROVIDER` env var | `gitea` |
| Credential type | Gitea API token |
| Project ID format | `owner/repo` (e.g. `alice/my-service`) |
| Webhook signature | `X-Gitea-Signature` header — HMAC-SHA256, **plain hex digest** (no `sha256=` prefix) |
| Webhook events | `push`, `pull_request`, `issue_comment` |
| oauth2-proxy provider | `gitea` |

> **Signature format note:** Gitea's HMAC-SHA256 signature in `X-Gitea-Signature` is a plain hexadecimal digest with no prefix — unlike GitHub and Bitbucket which both use `sha256=<hex>`. Phalanx handles this automatically; just be aware of the difference if you are debugging webhook deliveries.

---

## Step 1 — Create a Gitea API token

1. Log into your Gitea instance as the user (or service account) Phalanx will act as.
2. Go to **User Settings → Applications → Manage Access Tokens → Generate Token**.
3. Give it a name (e.g. `phalanx`).
4. Select **Token Permissions**:

   | Permission | Level |
   |---|---|
   | Repository | Read and Write |
   | Issue | Read and Write |
   | Pull Request | Read and Write |
   | Notification | (not needed) |

   > If your Gitea version uses the older single-scope model, select `repo` scope.

5. Click **Generate Token** and copy the value.

Set this as `GITEA_TOKEN` in the gateway environment (or `gitea.token` in Helm values).

Also set `GITEA_URL` to the root URL of your Gitea instance, e.g. `https://gitea.example.com`. The default (`https://gitea.com`) is only correct if you are using the public Gitea.com service.

---

## Step 2 — Create a Gitea OAuth2 application (for dashboard login)

1. In Gitea, go to **Site Administration → Integrations → OAuth2 Applications** (admin users only), or ask your Gitea admin to create the application.

   Alternatively, create it at the user level: **User Settings → Applications → OAuth2 Applications → Create OAuth2 Application**.

2. Fill in:
   - **Application Name:** `phalanx`
   - **Redirect URI:** `https://phalanx.example.com/oauth2/callback`

3. Click **Create Application** and copy the **Client ID** and **Client Secret**.

oauth2-proxy supports Gitea natively with `--provider=gitea`. It forwards the authenticated user's access token as `X-Forwarded-Access-Token` (with `--pass-access-token=true`), which the gateway uses for user-scoped project search.

You also need to tell oauth2-proxy where your Gitea instance lives:

```
--gitea-url=https://gitea.example.com
```

---

## Step 3 — Configure a webhook on your repository

For each Gitea repository you want Phalanx to watch:

1. Go to **Repository → Settings → Webhooks → Add Webhook → Gitea**.
2. Set **Target URL** to `https://phalanx.example.com/webhook/gitea`.
3. Set **HTTP Method** to `POST`.
4. Set **Content Type** to `application/json`.
5. Set **Secret** to the same random value you will use for `GITEA_WEBHOOK_SECRET`.
6. Under **Trigger On**, select:
   - **Push**
   - **Pull Request**
   - **Issue Comment**
7. Check **Active** and click **Add Webhook**.

**Generate a webhook secret:**

```bash
openssl rand -hex 32
```

---

## Step 4 — Add `.agents/config.yaml` to your repository

Without this file no automatic dispatch occurs (deny-by-default):

```yaml
# .agents/config.yaml
allowed_users:
  - your-gitea-username
  - another-team-member

gas_limit_input: 80000
gas_limit_output: 20000

prompt_mode: append
prompt: |
  This project uses Go 1.22 and follows the standard Go style guide.
```

Commit this to the default branch.

---

## Step 5 — Deploy

### Helm

```bash
helm install phalanx ./helm/phalanx \
  --namespace pi-agents --create-namespace \
  --set provider=gitea \
  --set gitea.url=https://gitea.example.com \
  --set gitea.token=<api-token> \
  --set gitea.webhookSecret=$(openssl rand -hex 32) \
  --set llm.apiKey=sk-... \
  --set llm.baseUrl=https://api.openai.com/v1 \
  --set llm.model=gpt-4o \
  --set oauth2proxy.provider=gitea \
  --set oauth2proxy.clientId=<oauth2-client-id> \
  --set oauth2proxy.clientSecret=<oauth2-client-secret> \
  --set oauth2proxy.cookieSecret=$(openssl rand -base64 32) \
  --set oauth2proxy.extraArgs[0]="--gitea-url=https://gitea.example.com" \
  --set ingress.host=phalanx.example.com
```

### Raw Kubernetes manifests

Edit `k8s/secrets.yaml` to fill in `gitea-creds`, set `PROVIDER=gitea` and `GITEA_URL` in `k8s/gateway-deployment.yaml`, and update the oauth2-proxy args in `k8s/oauth2-proxy-deployment.yaml` to use `--provider=gitea` with `--gitea-url`.

---

## TLS and custom certificates

Gitea is almost always self-hosted, often with a certificate from a private CA. If Phalanx containers cannot verify the Gitea TLS certificate, all API calls will fail.

Follow the [custom root certificate guide](../walkthrough.md#part-5--custom-root-certificates-enterprise-environments) to mount your CA bundle, or in Helm:

```yaml
# values.yaml
customCACerts: |
  -----BEGIN CERTIFICATE-----
  MIIBxTCCAW...your internal CA...
  -----END CERTIFICATE-----
```

---

## Environment variables reference

| Variable | Required | Description |
|---|---|---|
| `PROVIDER` | Yes | Set to `gitea` |
| `GITEA_TOKEN` | Yes | API token with repository and pull request read/write permissions |
| `GITEA_URL` | Yes (self-hosted) | Root URL of your Gitea instance (default: `https://gitea.com`) |
| `GITEA_WEBHOOK_SECRET` | Yes | Shared secret for HMAC-SHA256 webhook signature verification |

---

## Troubleshooting

**Webhooks delivered but no job appears**
- Check `allowed_users`. Gitea sends the actor's login name (same as the username shown in the Gitea UI).
- In Gitea, go to **Repository → Settings → Webhooks → (your webhook) → Recent Deliveries** to inspect the response.

**"401 Unauthorized" in gateway logs**
- The API token may have expired or been revoked. Generate a new one in **User Settings → Applications**.

**Inline PR review comments not appearing**
- Gitea's inline comment API requires a pull request review object. Confirm your Gitea version is 1.19 or later, as the reviews endpoint was not available in older releases.

**Dashboard login redirects to wrong URL**
- The `--gitea-url` flag passed to oauth2-proxy must match `GITEA_URL`. A mismatch causes the OAuth discovery to point at the wrong instance.

**TLS / certificate errors**
- Mount your private CA certificate as described in the TLS section above.
