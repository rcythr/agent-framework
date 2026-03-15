# GitLab OAuth2 Application Setup

This document describes the manual steps required to configure a GitLab OAuth2 application for the `pi-agent-gateway` dashboard.

## Steps

1. In GitLab, go to **User Settings → Applications → Add new application** (or use a Group-level application under **Group → Settings → Applications**).

2. Fill in the application details:
   - **Name:** `pi-agent-gateway`
   - **Redirect URI:** `https://<your-domain>/oauth2/callback`
   - **Scopes:** `api`, `read_user`, `openid`

   The `api` scope is required so that oauth2-proxy can forward a usable access
   token to the gateway via the `X-Forwarded-Access-Token` header.  The gateway
   uses this token for user-scoped provider calls — project search and webhook
   registration — so without it those features will not work.

3. Click **Save application**.

4. Copy the **Application ID** and **Secret** shown on the confirmation page.

5. Base64-encode the values and populate the `oauth2-proxy-creds` Kubernetes secret:

   ```bash
   # Generate a random cookie secret (32 bytes, base64-encoded, then base64-encoded again for K8s)
   COOKIE_SECRET=$(openssl rand -base64 32 | tr -d '\n' | base64)

   # Encode the GitLab application ID and secret
   CLIENT_ID=$(echo -n "<your-application-id>" | base64)
   CLIENT_SECRET=$(echo -n "<your-application-secret>" | base64)

   kubectl -n pi-agents patch secret oauth2-proxy-creds \
     --type='json' \
     -p="[
       {\"op\": \"replace\", \"path\": \"/data/cookie-secret\", \"value\": \"$COOKIE_SECRET\"},
       {\"op\": \"replace\", \"path\": \"/data/client-id\",     \"value\": \"$CLIENT_ID\"},
       {\"op\": \"replace\", \"path\": \"/data/client-secret\", \"value\": \"$CLIENT_SECRET\"}
     ]"
   ```

6. Restart the `oauth2-proxy` deployment to pick up the new credentials:

   ```bash
   kubectl -n pi-agents rollout restart deployment/oauth2-proxy
   ```

## Notes

- The redirect URI must exactly match what GitLab has on record; a mismatch will cause login to fail.
- The `--gitlab-group` flag in the oauth2-proxy deployment restricts access to members of a specific GitLab group. Update the `gateway-config` ConfigMap key `gitlab-group` accordingly.
- For local development with KIND, replace `<your-domain>` with `phalanx.localhost`.
